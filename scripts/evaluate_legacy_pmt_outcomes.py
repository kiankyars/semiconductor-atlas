#!/usr/bin/env python3
"""Add a reviewed legacy-source outcome without rewriting a frozen PMT screen.

Legacy snapshot/ingestion records are not per-request HTTP ledgers. This bounded
adapter preserves that distinction and never weakens the modern capture adapter.
The first rule handles the retained TSMC first-Phoenix-facility HVM narrative,
not the aggregate Fab 21 operating-table commercial-commencement observation.
"""
from __future__ import annotations

import argparse
import copy
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import evaluate_source_guidance as guidance
from scripts import frozen_runtime as runtime
from scripts import verify_pmt_outcome_screen as population_screen
from semiconductor_atlas.ai_critical import _html_visible_text
from semiconductor_atlas import source_guidance_outcomes as arithmetic

REVIEW_FORMAT = 'semiconductor-atlas-legacy-pmt-outcome-review-v1'
REPORT_FORMAT = 'semiconductor-atlas-legacy-pmt-outcome-diagnostic-v1'
RULE = 'tsmc_phoenix_first_facility_hvm_end_year_v1'
SOURCE_ID = 'tsmc-2025-20f'
SOURCE_URL = 'https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm'
SOURCE_SHA = 'c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b'
SOURCE_REFERENCE = 'source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html'
PROVENANCE_REFERENCE = 'baselines/ai_critical_manufacturing_v1.json'
MANIFEST_REFERENCE = 'source_snapshots/2026-08-20-ai-critical-manufacturing-v1/manifest.json'
CASE_ID = 'nist2024-d67-tsmc-phoenix-first-fab--timing-01'
LEGACY_REFERENCE_ID = 'legacy-tsmc-2025-20f-first-phoenix-hvm'
BOUNDARIES = dict.fromkeys((*population_screen.BOUNDARIES,
    'historical_http_transaction_verified', 'external_timestamp_attestation',
    'independent_physical_corroboration', 'canonical_fab21_phase_identity'), False)
RIGHTS = {'use': 'local_review_only', 'raw_redistribution': False,
          'publisher_bodies_embedded': False, 'new_network_acquisition': False,
          'model_training': False}
ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = tuple(sorted(set(population_screen.CODE_PATHS) | {
    'scripts/evaluate_legacy_pmt_outcomes.py'}))
LOADED_CODES = {name: runtime.digest(runtime.read(ROOT / name)) for name in CODE_PATHS}


def now():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def one(rows, label):
    if len(rows) != 1:
        raise ValueError('exactly one ' + label + ' required')
    return rows[0]


def checked_bytes(raw, descriptor, *, limit, reference=None):
    runtime._keys(descriptor, {'path', 'bytes', 'sha256'})
    runtime._hex(descriptor['sha256'], 64)
    if (not isinstance(raw, bytes) or type(descriptor['bytes']) is not int
            or not 0 < descriptor['bytes'] <= limit or len(raw) != descriptor['bytes']
            or runtime.digest(raw) != descriptor['sha256']
            or (reference is not None and descriptor['path'] != reference)):
        raise ValueError('exact bounded legacy input identity differs')


def validate_legacy_source(review, body, provenance_raw, manifest_raw):
    """Verify retained identities and recorded lineage, not historic HTTP facts."""
    inputs = review['legacy_inputs']
    runtime._keys(inputs, {'body', 'provenance', 'snapshot_manifest'})
    checked_bytes(body, inputs['body'], limit=20_000_000, reference=SOURCE_REFERENCE)
    checked_bytes(provenance_raw, inputs['provenance'], limit=2_000_000,
                  reference=PROVENANCE_REFERENCE)
    checked_bytes(manifest_raw, inputs['snapshot_manifest'], limit=1_000_000,
                  reference=MANIFEST_REFERENCE)
    provenance = guidance.strict_json(provenance_raw)
    manifest = guidance.strict_json(manifest_raw)
    if (provenance.get('format') != 'semiconductor-atlas-ai-critical-input-v1'
            or manifest.get('format') != 'semiconductor-atlas-source-inputs-v1'):
        raise ValueError('supported legacy provenance and snapshot formats required')
    for value, key in ((provenance, 'sources'), (provenance, 'ingestion_runs'), (manifest, 'inputs')):
        rows = value.get(key)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000 or any(not isinstance(row, dict) for row in rows):
            raise ValueError('bounded legacy record arrays required')
    source = one([row for row in provenance['sources'] if row.get('source_id') == SOURCE_ID], 'legacy source')
    capture = one([row for row in manifest['inputs'] if row.get('path') == Path(SOURCE_REFERENCE).name],
                  'legacy snapshot member')
    expected = {'source_id': SOURCE_ID, 'url': SOURCE_URL, 'archive_path': SOURCE_REFERENCE,
        'bytes': len(body), 'content_sha256': runtime.digest(body), 'media_type': 'text/html',
        'source_type': 'official_primary', 'source_family': 'sec-edgar-filing',
        'publisher': 'Taiwan Semiconductor Manufacturing Company Limited',
        'published_at_precision': 'day', 'adapter_version': 'official-primary-exact-byte-capture-v1'}
    if any(source.get(key) != value for key, value in expected.items()):
        raise ValueError('legacy source identity, media or publication precision differs')
    field_pairs = {'bytes': 'bytes', 'sha256': 'content_sha256', 'url': 'url',
        'content_type': 'media_type', 'publisher': 'publisher', 'published_at': 'published_at',
        'published_at_basis': 'published_at_basis', 'published_at_precision': 'published_at_precision',
        'retrieved_at': 'retrieved_at', 'acquired_at': 'acquired_at', 'adapter_version': 'adapter_version',
        'language': 'language'}
    if any(capture.get(left) != source.get(right) for left, right in field_pairs.items()):
        raise ValueError('snapshot and baseline source metadata disagree')
    if capture.get('record_type') != 'sec_filing':
        raise ValueError('legacy SEC filing record type required')
    for field in ('publisher', 'published_at_basis', 'adapter_version', 'language'):
        arithmetic._text(source.get(field), 'recorded legacy source field')
    snapshot_rights = capture.get('rights')
    source_rights = source.get('rights')
    if (not isinstance(snapshot_rights, dict) or not isinstance(source_rights, dict)
            or any(source_rights.get(key) != value for key, value in snapshot_rights.items())
            or source_rights.get('access') != 'public'):
        raise ValueError('legacy rights records disagree')
    for field in ('attribution', 'license', 'license_url', 'redistribution', 'reviewed_at'):
        arithmetic._text(snapshot_rights.get(field), 'recorded rights field')
    guidance.instant(review['reviewed_at'])
    published = arithmetic._day(source['published_at'])
    retrieved, acquired = (guidance.instant(source[key]) for key in ('retrieved_at', 'acquired_at'))
    run = one([row for row in provenance['ingestion_runs']
               if row.get('run_id') == source.get('ingestion_run_id')], 'legacy ingestion run')
    if (run.get('source_id') != SOURCE_ID or run.get('outcome') != 'succeeded'
            or run.get('inputs') != [{'source_id': SOURCE_ID,
                'content_sha256': source['content_sha256'], 'document_role': 'primary'}]
            or run.get('code_version') != source['adapter_version']):
        raise ValueError('legacy ingestion lineage differs')
    if not (published <= retrieved.date() and retrieved <= acquired
            <= guidance.instant(run['started_at']) <= guidance.instant(run['finished_at'])
            <= guidance.instant(provenance['recorded_at']) <= guidance.instant(review['reviewed_at'])
            <= guidance.instant(now())):
        raise ValueError('legacy publication, retrieval, ingestion and review clocks disagree')
    basis = manifest.get('retrieval_timestamp_basis')
    arithmetic._text(basis, 'recorded retrieval precision')
    if 'batch-completion' not in basis:
        raise ValueError('legacy batch-completion clock basis must remain explicit')
    return {'kind': 'reviewed_legacy_recorded_primary', 'source': copy.deepcopy(source),
        'snapshot_member': copy.deepcopy(capture), 'ingestion_run_sha256': runtime.digest(runtime.canonical(run)),
        'provenance_recorded_at': provenance['recorded_at'], 'retrieval_timestamp_basis': basis,
        'historical_http_transaction_verified': False, 'external_timestamp_attestation': False,
        'source_document_date': source['published_at'], 'legacy_inputs': copy.deepcopy(inputs)}


def first_phoenix_hvm(body, span):
    runtime._keys(span, {'start', 'end', 'sha256'})
    start, end = span['start'], span['end']
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body)
            or runtime.digest(body[start:end]) != span['sha256']):
        raise ValueError('exact complete Arizona narrative span differs')
    text = _html_visible_text(body[start:end].decode('utf-8'))
    anchors = ('TSMC Arizona.', 'multiple advanced semiconductor manufacturing facilities in Phoenix, Arizona.',
               'Construction of our second facility is ongoing', 'construction of our third facility commenced')
    if not text.startswith(anchors[0]) or any(anchor not in text for anchor in anchors[1:]):
        raise ValueError('complete first/second/third Phoenix-facility narrative required')
    years = re.findall(r'Our first facility successfully entered high volume production at the end of (20\d{2})\.', text)
    year = one(years, 'first-facility HVM end-year statement')
    return {'status': 'observed_by', 'through': year + '-12-31'}


def overlay(base_review, review, legacy):
    """Change exactly one matched outcome; never change the roster or other cases."""
    current = one([row for row in base_review['roster'] if row['case_id'] == CASE_ID], 'original first-fab case')
    project = 'nist2024-d67-tsmc-phoenix-first-fab'
    expected = {'case_id': CASE_ID, 'project_id': project, 'project_group': project,
        'event_type': 'high_volume_production', 'scope_id': project + '--original-pmt-scope',
        'source_reference_ids': ['nist2024-d67']}
    if any(current.get(key) != value for key, value in expected.items()):
        raise ValueError('original first-Phoenix-fab HVM identity differs')
    change = review['outcome']
    runtime._keys(change, arithmetic.IDENTITY | {'reason', 'outcome', 'source_reference_ids', 'source_document_date'})
    if (any(change[key] != current[key] for key in arithmetic.IDENTITY)
            or change['source_reference_ids'] != [LEGACY_REFERENCE_ID]
            or change['source_document_date'] != legacy['source_document_date']
            or change['outcome'] != legacy['derived_outcome']):
        raise ValueError('legacy outcome identity, source day or conservative date bound differs')
    correspondence = review['correspondence']
    runtime._keys(correspondence, {'guidance_reference_id', 'organization', 'location', 'phase', 'event', 'limitations'})
    if correspondence['guidance_reference_id'] != 'nist2024-d67':
        raise ValueError('correspondence requires original PMT primary reference')
    for field in ('organization', 'location', 'phase', 'event', 'limitations'):
        arithmetic._text(correspondence[field], 'manual cross-source correspondence')
    if LEGACY_REFERENCE_ID in base_review['source_references']:
        raise ValueError('legacy reference cannot replace an existing reference')
    outcomes = copy.deepcopy(base_review['outcomes'])
    original = one([row for row in outcomes if row['case_id'] == CASE_ID], 'original outcome')
    if original['outcome'] != {'status': 'unknown'}:
        raise ValueError('this rule only fills a previously unknown original outcome')
    outcomes[outcomes.index(original)] = copy.deepcopy(change)
    return arithmetic.score(copy.deepcopy(base_review['roster']), outcomes)


def codes():
    current = {name: runtime.digest(runtime.read(ROOT / name)) for name in CODE_PATHS}
    if current != LOADED_CODES:
        raise ValueError('producer changed after module loading')
    return current


def evaluate(review_path, review_sha256, reference_root, source_body, provenance, source_manifest):
    runtime._hex(review_sha256, 64)
    raw = runtime.read(review_path, limit=1_000_000)
    if runtime.digest(raw) != review_sha256:
        raise ValueError('trusted legacy review hash differs')
    review = guidance.strict_json(raw)
    runtime._keys(review, {'format', 'rule', 'reviewed_at', 'reviewed_by', 'selection', 'prior_exposure',
        'rights', 'boundaries', 'parent_screen', 'legacy_inputs', 'evidence', 'correspondence', 'outcome'})
    if (review['format'] != REVIEW_FORMAT or review['rule'] != RULE or review['rights'] != RIGHTS
            or review['boundaries'] != BOUNDARIES
            or any(value is not False for value in review['boundaries'].values())
            or any(review['rights'][key] is not False for key in RIGHTS if key != 'use')):
        raise ValueError('explicit legacy rule, local-only rights and false boundaries required')
    for field in ('reviewed_by', 'selection', 'prior_exposure'):
        arithmetic._text(review[field], 'exposed legacy review context')
    runtime._keys(review['parent_screen'], {'path', 'bytes', 'sha256'})
    guidance.relative(review['parent_screen']['path'])
    parent_path = Path(reference_root) / review['parent_screen']['path']
    parent_raw = runtime.read(parent_path, limit=1_000_000)
    checked_bytes(parent_raw, review['parent_screen'], limit=1_000_000)
    parent = guidance.strict_json(parent_raw)
    parent_verification = population_screen.verify(parent_path, review['parent_screen']['sha256'], reference_root)
    if not guidance.instant(parent['reviewed_at']) <= guidance.instant(review['reviewed_at']) <= guidance.instant(now()):
        raise ValueError('new review cannot precede its frozen parent or lie in the future')
    base_descriptor = parent['diagnostic_review']
    base_path = Path(reference_root) / guidance.relative(base_descriptor['path'])
    base = guidance.load_review(base_path, base_descriptor['sha256'], reference_root)['review']
    body = runtime.read(source_body, limit=20_000_000)
    if runtime.digest(body) != SOURCE_SHA:
        raise ValueError('this rule requires the exact previously retained TSMC filing')
    legacy = validate_legacy_source(review, body, runtime.read(provenance, limit=2_000_000),
                                    runtime.read(source_manifest, limit=1_000_000))
    runtime._keys(review['evidence'], {'arizona_narrative'})
    legacy['derived_outcome'] = first_phoenix_hvm(body, review['evidence']['arizona_narrative'])
    if arithmetic._day(legacy['derived_outcome']['through']) > arithmetic._day(legacy['source_document_date']):
        raise ValueError('reported completed event cannot extend beyond the source document day')
    result = overlay(base, review, legacy)
    supplemental = {row['case_id'] for row in parent['supplemental_cases']}
    def counts(rows):
        return {'cases': len(rows), 'project_groups': len({row['case']['project_group'] for row in rows}),
            'scored_cases': sum(row['target_met'] is not None for row in rows),
            'unknown_outcomes': sum(row['outcome_status'] == 'unknown' for row in rows)}
    return {'review_sha256': review_sha256, 'review': review, 'legacy_source_verification': legacy,
        'parent_screen_verification': parent_verification, 'diagnostic': result,
        'original': counts([row for row in result['cases'] if row['case']['case_id'] not in supplemental]),
        'supplemental': counts([row for row in result['cases'] if row['case']['case_id'] in supplemental]),
        'verification_scope': 'Exact retained legacy body, snapshot and ingestion lineage plus a manually reviewed '
            'first-Phoenix-facility HVM correspondence. Legacy batch timestamps are not HTTP transaction '
            'attestation. Original roster and targets remain unchanged. This is outcome-exposed source-statement '
            'arithmetic, not historical system forecasting, independent physical corroboration or calibration.'}


def create(*, output, **inputs):
    started = now()
    result = evaluate(**inputs)
    if guidance.instant(result['review']['reviewed_at']) > guidance.instant(started):
        raise ValueError('execution cannot precede review')
    report = {'format': REPORT_FORMAT, 'execution_started_at': started, 'execution_finished_at': now(),
              'producer_files': codes(), 'result': result}
    raw = runtime.canonical(report)
    runtime.write_new(output, raw)
    return {'path': str(output), 'bytes': len(raw), 'sha256': runtime.digest(raw),
            'denominators': result['diagnostic']['denominators']}


def verify(*, report_path, report_sha256, **inputs):
    runtime._hex(report_sha256, 64)
    raw = runtime.read(report_path)
    if runtime.digest(raw) != report_sha256:
        raise ValueError('trusted report hash differs')
    report = guidance.strict_json(raw)
    runtime._keys(report, {'format', 'execution_started_at', 'execution_finished_at', 'producer_files', 'result'})
    result = evaluate(**inputs)
    if (report['format'] != REPORT_FORMAT or report['producer_files'] != codes()
            or report['result'] != result or runtime.canonical(report) != raw):
        raise ValueError('exact legacy outcome reconstruction or producer differs')
    if not (guidance.instant(result['review']['reviewed_at']) <= guidance.instant(report['execution_started_at'])
            <= guidance.instant(report['execution_finished_at']) <= guidance.instant(now())):
        raise ValueError('actual execution chronology differs')
    return {'verified': True, 'sha256': report_sha256, 'scope': result['verification_scope']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('review', 'review-sha256', 'reference-root', 'source-body', 'provenance', 'source-manifest'):
        parser.add_argument('--' + field, required=True)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('create').add_argument('--output', required=True)
    check = sub.add_parser('verify')
    check.add_argument('--report', required=True)
    check.add_argument('--report-sha256', required=True)
    args = parser.parse_args()
    inputs = {'review_path': args.review, 'review_sha256': args.review_sha256,
        'reference_root': args.reference_root, 'source_body': args.source_body,
        'provenance': args.provenance, 'source_manifest': args.source_manifest}
    result = create(output=args.output, **inputs) if args.operation == 'create' else verify(
        report_path=args.report, report_sha256=args.report_sha256, **inputs)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
