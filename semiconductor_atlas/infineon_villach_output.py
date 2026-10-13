"""Admit five manually reviewed wafer-area totals from one retained issuer PDF."""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scripts import frozen_runtime as runtime
from . import database, models, realized_milestones, repository, source_checks, source_quantities


RULE = 'infineon-villach-produced-wafer-area-v1'
FORMAT = 'semiconductor-atlas-villach-output-admission-review-v1'
DECISION = 'accept_all_five_source_native_site_wafer_area_totals'
SOURCE_SHA = '1606c4ffdb5f4b9f5118f9f003d423c5715b3fb32a8f20d0c712219d9ecba032'
RESEARCH_SHA = '92ba8eaf4b9951f0b963ff9cedccbec0a83215e0ce835e6bff6b64b60402e270'
SOURCE_URL = 'https://www.infineon.com/row/public/documents/regions/austria/2025-uwe-de-web.pdf'
SITE_KEY = 'infineon-issuer:villach-site-unresolved'
SITE = {'kind': 'site', 'stable_key': SITE_KEY,
        'label': 'Infineon Villach site (source-native, canonical identity unresolved)',
        'scope': 'Villach site aggregate; not Austria-wide operations, one building or production line, or the cross-country One Virtual Fab network.'}
DEFINITION = ('Publisher-produced wafer surface area across the Villach site diameter mix, used as an '
              'environmental-indicator denominator. Not cleanroom area, wafer starts/counts, layer-weighted '
              'workload, shipments, installed capacity or qualified good-die output. Product/test/reclaim '
              'accounting is not exhaustively specified; process mix limits comparability.')
RIGHTS = {'use': 'local_informational_noncommercial_review_only', 'redistribution_authorized': False,
          'commercial_use_authorized': False, 'model_training_authorized': False,
          'attribution': 'Infineon Technologies Austria AG and Infineon Technologies AG',
          'basis': 'Retained Infineon Terms of Use section 3.2; original materials and notices remain unchanged.'}
BOUNDARIES = dict.fromkeys(('physical_capacity_basis_assigned', 'native_diameter_allocation',
    'quarterly_allocation', 'wafer_count_conversion', 'yield_utilization_qualification_inferred',
    'canonical_identity_assigned', 'geometry_inferred', 'ownership_assigned', 'independent_measurement',
    'forecast_created', 'forecast_scored', 'baseline_modified', 'scheduled_execution_rerouted',
    'raw_redistribution', 'model_training', 'source_acquisition'), False)


def now():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def clock(value):
    return datetime.fromisoformat(models._iso_timestamp(value, 'clock').replace('Z', '+00:00'))


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
                    root / 'scripts/admit_infineon_villach_output.py', root / 'scripts/frozen_runtime.py'])
    return {str(p.relative_to(root)): runtime.digest(runtime.read(p)) for p in paths}


_LOADED_CODES = codes()


def rows():
    result = []
    for year, literal in zip(range(2020, 2025), ('462.5', '488.9', '517.8', '524.0', '505.5')):
        label = f'{year}/{str(year + 1)[2:]}'
        metadata = asdict(source_quantities.SourceQuantityMetadata('not-yet-admitted',
            'produced_wafer_area', 'source_native_site', SITE['scope'], 'fiscal_year',
            f'{year}-10-01', f'{year + 1}-09-30', label, 'square_centimeters', None,
            literal, 1000000, 'as_reported_precision_unspecified', 'reported', DEFINITION))
        metadata.pop('claim_version_id')
        result.append({'key': 'produced_wafer_area:' + label, 'metadata': metadata,
                       'source_display_number_literal': literal.replace('.', ','),
                       'support_page': 39, 'context_pages': [2, 24, 25, 38, 65, 68]})
    return result


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
        raise ValueError('trusted Villach admission review hash mismatch')
    review = realized_milestones._json(raw)
    runtime._keys(review, {'format', 'decision', 'reviewed_at', 'reviewed_by', 'prior_exposure',
        'selection', 'parent_database_sha256', 'research_review', 'input_files', 'site', 'rows',
        'rights', 'boundaries', 'interpretation_method'})
    if review['format'] != FORMAT or review['decision'] != DECISION:
        raise ValueError('explicit source-specific Villach admission review required')
    for field in ('reviewed_by', 'prior_exposure', 'selection', 'interpretation_method'):
        models._required(review[field], field)
    clock(review['reviewed_at'])
    runtime._hex(review['parent_database_sha256'], 64)
    if review['site'] != SITE or review['rights'] != RIGHTS or review['boundaries'] != BOUNDARIES:
        raise ValueError('Villach site, rights or excluded-inference boundaries differ')
    if any(value is not False for value in review['boundaries'].values()):
        raise ValueError('Villach boundaries must be boolean false')
    if any(review['rights'][field] is not False for field in
           ('redistribution_authorized', 'commercial_use_authorized', 'model_training_authorized')):
        raise ValueError('Villach rights permissions must be boolean false')
    if review['rows'] != rows():
        raise ValueError('complete five-row chart selection or its semantics differs')
    runtime._keys(review['research_review'], {'path', 'sha256'})
    relative(review['research_review']['path'])
    if review['research_review']['sha256'] != RESEARCH_SHA:
        raise ValueError('the reviewed retained-PDF interpretation differs')
    if not isinstance(review['input_files'], dict) or not 1 <= len(review['input_files']) <= 60:
        raise ValueError('bounded complete evidence inventory required')
    for name, item in review['input_files'].items():
        relative(name)
        runtime._keys(item, {'sha256', 'bytes'})
        runtime._hex(item['sha256'], 64)
        if type(item['bytes']) is not int or not 0 < item['bytes'] <= 40_000_000:
            raise ValueError('invalid evidence byte bound')
    return review


def snapshot(review, reference_root):
    result = {}
    for name, expected in review['input_files'].items():
        raw = runtime.read(Path(reference_root) / name, limit=40_000_000)
        if len(raw) != expected['bytes'] or runtime.digest(raw) != expected['sha256']:
            raise ValueError('retained Villach input bytes differ: ' + name)
        result[name] = raw
    if sum(map(len, result.values())) > 60_000_000:
        raise ValueError('evidence inventory exceeds total byte bound')
    return result


def _packet_names(root, packet):
    path = Path(root) / relative(packet)
    descriptor = runtime._directory(path)
    import os
    os.close(descriptor)
    names = set()
    for p in path.rglob('*'):
        if p.is_symlink():
            raise ValueError('symlinks are not retained packet members')
        if p.is_file():
            names.add(p.relative_to(root).as_posix())
    return names


def load_evidence(review, payloads, reference_root):
    """Validate private exact copies; visual interpretation remains explicitly reviewed."""
    research_name = review['research_review']['path']
    if runtime.digest(payloads.get(research_name, b'')) != RESEARCH_SHA:
        raise ValueError('retained source research review is missing or changed')
    research = realized_milestones._json(payloads[research_name])
    source = research['source']
    if (source['sha256'] != SOURCE_SHA or source['url'] != SOURCE_URL
            or source['publication_date'] is not None or source['pdf_pages'] != 72
            or research['claim_acceptance'] is not False or research['collector_enabled'] is not False):
        raise ValueError('research is not the retained, unadmitted Villach source review')
    required = {research_name, relative(research['acquisition']['plan'])}
    capture = relative(research['acquisition']['packet'])
    visual = relative(research['visual_review']['retained_visual_packet'])
    for packet, expected in ((capture, research['acquisition']['manifest_sha256']),
                             (visual, research['visual_review']['retained_visual_manifest_sha256'])):
        name = packet + '/manifest.json'
        if runtime.digest(payloads.get(name, b'')) != expected:
            raise ValueError('retained packet manifest differs')
        manifest = realized_milestones._json(payloads[name])
        members = {relative(packet + '/' + member) for member in manifest['files']}
        expected_names = members | {name}
        if _packet_names(reference_root, packet) != expected_names:
            raise ValueError('complete retained packet inventory differs')
        required |= expected_names
        for member, identity in manifest['files'].items():
            body = payloads.get(packet + '/' + member, b'')
            if len(body) != identity['bytes'] or runtime.digest(body) != identity['sha256']:
                raise ValueError('retained packet member differs')
        if manifest['raw_redistribution'] is not False or manifest['claim_acceptance'] is not False:
            raise ValueError('capture or visual packet cannot authorize publication or claims')
    plan = realized_milestones._json(payloads[capture + '/plan.json'])
    if (payloads[research['acquisition']['plan']] != payloads[capture + '/plan.json']
            or runtime.digest(payloads[capture + '/plan.json']) != research['acquisition']['plan_sha256']
            or plan['decision'] != 'approved_exact_urls_local_informational_noncommercial_review_only'
            or any(plan[field] is not False for field in ('raw_redistribution', 'claim_acceptance',
                                                         'collector_enabled', 'redirects', 'retries', 'cookies'))):
        raise ValueError('retained exact-URL local-use acquisition approval differs')
    probe_roots = set()
    for i, dep in enumerate(plan['dependencies']):
        name = relative(dep['path'])
        body = payloads.get(name, b'')
        if runtime.digest(body) != dep['sha256'] or body != payloads[capture + f'/dependencies/{i}.body']:
            raise ValueError('original and copied policy dependencies differ')
        required.add(name)
        if name.endswith('/source_checks.json'):
            probe = str(PurePosixPath(name).parent)
            probe_roots.add(probe)
            required.add(probe + '/plan.json')
    if len(probe_roots) != 2:
        raise ValueError('both retained policy probes are required')
    for probe in probe_roots:
        if _packet_names(reference_root, probe) != {n for n in required if n.startswith(probe + '/')}:
            raise ValueError('policy probe inventory differs')
    if set(payloads) != required:
        raise ValueError('evidence graph contains missing or unrelated files')
    if (runtime.digest(payloads.get(source['retained_path'], b'')) != SOURCE_SHA
            or len(payloads[source['retained_path']]) != source['bytes']):
        raise ValueError('exact reviewed PDF bytes are required')
    with tempfile.TemporaryDirectory(prefix='atlas-villach-evidence-') as directory:
        private = Path(directory).resolve()
        for name, body in payloads.items():
            path = private / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(body)
        reports = [source_checks.validate_source_checks(private / p / 'source_checks.json', private / p)
                   for p in sorted(probe_roots) + [capture]]
    if sum(r['attempt_count'] for r in reports) != 4 or any(g['failed'] for r in reports for g in r['groups']):
        raise ValueError('the four retained successful acquisition attempts are required')
    ledger = realized_milestones._json(payloads[capture + '/source_checks.json'])
    attempts = ledger['attempts']
    if {a['url'] for a in attempts} != {e['url'] for e in plan['entries']}:
        raise ValueError('captured exact URLs differ from reviewed acquisition plan')
    matched = [a for a in attempts if a['url'] == SOURCE_URL]
    if len(matched) != 1 or matched[0]['sha256'] != SOURCE_SHA or matched[0]['finished_at'] != source['retrieval_finished_at']:
        raise ValueError('PDF retrieval identity or actual clock differs')
    for p in probe_roots:
        for a in realized_milestones._json(payloads[p + '/source_checks.json'])['attempts']:
            if clock(a['finished_at']) > min(clock(a['started_at']) for a in attempts):
                raise ValueError('policy acquisition must precede document acquisition')
    for a in attempts:
        if (not clock(plan['reviewed_at']) <= clock(a['started_at']) <= clock(a['finished_at']) < clock(plan['expires_at'])
                or clock(a['finished_at']) > clock(research['review_completed_at'])
                or a['redirect_count'] != 0):
            raise ValueError('capture and review chronology or no-redirect contract differs')
    if clock(research['review_completed_at']) > clock(review['reviewed_at']):
        raise ValueError('claim review precedes its source interpretation')
    return {'research': research, 'source': source, 'input_files': review['input_files']}


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


def populate(connection, review, data, review_sha256, started, admitted, code_sha256):
    if not clock(review['reviewed_at']) <= clock(started) < clock(admitted):
        raise ValueError('Villach review/admission chronology differs')
    source = data['source']
    family_key, source_key = 'infineon-issuer', 'issuer-pdf:' + SOURCE_URL
    family_id, source_id, entity_id = [repository.stable_id(k) for k in (family_key, source_key, SITE_KEY)]
    run_id = repository.stable_id(RULE, review_sha256)
    if (connection.execute('SELECT 1 FROM entities WHERE id=? OR stable_key=?', (entity_id, SITE_KEY)).fetchone()
            or connection.execute('SELECT 1 FROM sources WHERE id=? OR stable_key=?', (source_id, source_key)).fetchone()):
        raise ValueError('Villach source/site already admitted; this is not a refresh importer')
    if database.apply_migrations(connection, target_version=8) != [8]:
        raise ValueError('only the version-8 source-quantity extension is expected')
    connection.execute('UPDATE schema_migrations SET applied_at=? WHERE version=8', (admitted,))
    family = connection.execute('SELECT * FROM source_families WHERE stable_key=?', (family_key,)).fetchone()
    if family:
        family_id = family['id']
    else:
        repository.add_source_family(connection, models.SourceFamily(family_id, family_key,
            'Infineon issuer disclosures', admitted))
    license_label = 'Local informational non-commercial review only; no Atlas redistribution or training approval'
    publisher = RIGHTS['attribution']
    repository.add_source(connection, models.Source(source_id, family_id, source_key,
        'Infineon Villach environmental statement 2025', publisher, SOURCE_URL, admitted, license_label))
    document_id = repository.stable_id(source_id, SOURCE_SHA, source['retrieval_finished_at'])
    repository.add_source_document(connection, models.SourceDocument(document_id, source_id, SOURCE_URL,
        'Infineon Villach environmental statement 2025: produced wafer area', source['retrieval_finished_at'],
        SOURCE_SHA, published_at=None, media_type='application/pdf', license=license_label,
        metadata={'rights': RIGHTS, 'retained_body': source['retained_path'], 'pdf_pages': 72,
            'research_review': review['research_review'], 'source_native_site': SITE,
            'publication_date_basis': 'Unknown; PDF metadata and edition label are not publication dates.',
            'interpretation_method': review['interpretation_method']}))
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, source_id, started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256['semiconductor_atlas/infineon_villach_output.py'],
        document_id, {'rule_version': RULE, 'review_sha256': review_sha256, 'review': review,
            'code_sha256': code_sha256, 'interpreter': runtime.interpreter(), 'input_files': data['input_files'],
            'rights': RIGHTS, 'boundaries': BOUNDARIES, 'accepted_at': admitted,
            'acceptance_timestamp_basis': 'actual_database_admission',
            'interpretation_limits': data['research']['reviewed_output_series']['comparability_limits']}))
    repository.add_entity(connection, models.Entity(entity_id, models.EntityKind.SITE,
        SITE_KEY, admitted, SITE['label'], run_id))
    accepted = []
    for row in review['rows']:
        claim_id = repository.stable_id(RULE, review_sha256, row['key'], 'claim')
        quantity = source_quantities.SourceQuantityMetadata(claim_id, **row['metadata'])
        stable_key = quantity.series_key(entity_id)
        series_id = repository.stable_id(stable_key)
        repository.add_claim_series(connection, models.ClaimSeries(series_id, entity_id, stable_key,
            'source_quantity.' + quantity.metric, models.ValueKind.SCALAR, admitted))
        record_id = repository.stable_id(RULE, review_sha256, row['key'], 'record')
        payload = {'review_sha256': review_sha256, 'source_display_number_literal': row['source_display_number_literal'],
            'quantity': asdict(quantity), 'support_page': 39, 'context_pages': row['context_pages'],
            'admitted_at': admitted, 'physical_capacity_basis': None}
        repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document_id,
            row['key'], source['retrieval_finished_at'], models.source_record_payload_sha256(payload), payload))
        evidence = [models.EvidenceLink(document_id, models.EvidenceRole.SUPPORT, record_id,
            'PDF/printed page 39; complete five-total produced-wafer-area chart; fiscal year ' + quantity.source_period_label),
            models.EvidenceLink(document_id, models.EvidenceRole.CONTEXT, record_id,
            'PDF/printed pages 2,24,25,38,65,68; site, fiscal-year convention, process boundary, comparability and EMAS limits.')]
        with repository._atomic(connection):
            repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
                models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id,
                notes='Retrospective publisher-reported wafer-area total, manually reviewed; no physical capacity basis.'),
                quantity.scalar(), evidence=evidence)
            source_quantities.add_metadata(connection, quantity)
        accepted.append({'key': row['key'], 'claim_id': claim_id, 'quantity': asdict(quantity)})
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError('Villach successor validation failed: ' + str(issues[:3]))
    connection.commit()
    return {'run_id': run_id, 'admitted_at': admitted, 'source_native_site_id': entity_id,
            'source_quantities': accepted, 'boundaries': BOUNDARIES}


def admit(*, parent, review_path, reference_root, expected_review_sha256, output,
          verify_only=False, expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError('Villach implementation changed since module loading')
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
    connection = open_image(parent_raw, 7)
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
            raise ValueError('successor differs from complete parent-plus-output reconstruction')
        if (runtime.read(Path(review_path)) != review_raw
                or runtime.read(Path(parent), limit=1_000_000_000) != parent_raw
                or snapshot(review, reference_root) != payloads):
            raise ValueError('Villach evidence or parent changed during admission or replay')
        load_evidence(review, payloads, reference_root)
        if codes() != current_codes:
            raise ValueError('Villach implementation changed during admission or replay')
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError('successor changed during verification')
        if not verify_only:
            runtime.write_new(target, generated)
        return {**result, 'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
                'verified': verify_only, 'parent_modified': False, 'new_source_quantities': 5}
    finally:
        connection.close()
