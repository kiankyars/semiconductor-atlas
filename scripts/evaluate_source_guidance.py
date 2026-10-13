#!/usr/bin/env python3
"""Execute or verify a hash-pinned, manually reviewed source-statement diagnostic.

This adapter verifies retained bytes and reference membership, not the truth or
historical availability of publisher statements. Semantic and visual decisions
remain an explicit, hash-pinned review. It never opens or changes an Atlas DB.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import frozen_runtime as runtime
from semiconductor_atlas import source_guidance_outcomes as diagnostic

REVIEW_FORMAT = 'semiconductor-atlas-reviewed-source-guidance-screen-v1'
REPORT_FORMAT = 'semiconductor-atlas-retained-source-guidance-diagnostic-v1'
ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = tuple(sorted({'scripts/evaluate_source_guidance.py', 'scripts/frozen_runtime.py',
    *(path.relative_to(ROOT).as_posix() for suffix in ('*.py', '*.sql')
      for path in (ROOT / 'semiconductor_atlas').rglob(suffix))}))
LOADED_CODES = {name: runtime.digest(runtime.read(ROOT / name)) for name in CODE_PATHS}
BOUNDARIES = dict.fromkeys(('historical_availability_verified', 'historical_atlas_forecasts',
    'independent_or_blind_evaluation', 'representative_population', 'calibration',
    'model_training', 'physical_capacity_claims', 'database_modified',
    'prospective_study_modified', 'raw_redistribution'), False)


def now():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def instant(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value):
        raise ValueError('an actual UTC timestamp is required')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-finite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 500
            or PurePosixPath(value).is_absolute() or '\\' in value
            or any(part in ('', '.', '..') for part in value.split('/'))
            or value.split('/')[0] not in ('artifacts', 'review_plans')):
        raise ValueError('bounded repository-relative evidence path required')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('explicit review explanation required')


def load_review(path, expected_sha256, reference_root):
    runtime._hex(expected_sha256, 64)
    raw = runtime.read(path)
    if runtime.digest(raw) != expected_sha256:
        raise ValueError('trusted review hash mismatch')
    review = strict_json(raw)
    runtime._keys(review, {'format', 'reviewed_at', 'reviewed_by', 'selection', 'prior_exposure',
        'rights', 'boundaries', 'input_files', 'source_references', 'roster', 'outcomes'})
    if review['format'] != REVIEW_FORMAT or review['boundaries'] != BOUNDARIES:
        raise ValueError('exposed diagnostic contract or boundaries differ')
    if any(value is not False for value in review['boundaries'].values()):
        raise ValueError('diagnostic boundaries must remain false')
    instant(review['reviewed_at'])
    for field in ('reviewed_by', 'selection', 'prior_exposure', 'rights'):
        _text(review[field])
    inventory = review['input_files']
    if not isinstance(inventory, dict) or not 1 <= len(inventory) <= 100:
        raise ValueError('bounded evidence inventory required')
    payloads = {}
    for name, identity in inventory.items():
        relative(name)
        runtime._keys(identity, {'bytes', 'sha256'})
        runtime._hex(identity['sha256'], 64)
        if type(identity['bytes']) is not int or not 0 < identity['bytes'] <= 10_000_000:
            raise ValueError('bounded evidence size required')
        body = runtime.read(Path(reference_root) / name, limit=10_000_000)
        if len(body) != identity['bytes'] or runtime.digest(body) != identity['sha256']:
            raise ValueError('retained evidence differs: ' + name)
        payloads[name] = body
    if sum(map(len, payloads.values())) > 50_000_000:
        raise ValueError('total evidence bound exceeded')
    references = review['source_references']
    if not isinstance(references, dict) or not 1 <= len(references) <= 100:
        raise ValueError('bounded source references required')
    for key, reference in references.items():
        _text(key)
        runtime._keys(reference, {'kind', 'body_path', 'support_spans', 'interpretation',
                                 'source_document_date', 'capture_finished_at', 'source_url', 'capture'})
        if reference['kind'] not in ('reviewed_primary', 'screening_note'):
            raise ValueError('source reference kind is required')
        if relative(reference['body_path']) not in payloads:
            raise ValueError('reference body missing from inventory')
        _text(reference['interpretation'])
        if reference['source_document_date'] is not None:
            diagnostic._day(reference['source_document_date'])
        if reference['kind'] == 'reviewed_primary':
            if instant(reference['capture_finished_at']) > instant(review['reviewed_at']):
                raise ValueError('review cannot precede primary capture')
            url = urlsplit(reference['source_url'])
            if url.scheme != 'https' or not url.hostname or url.username or url.password:
                raise ValueError('public HTTPS primary-source URL required')
            capture = reference['capture']
            runtime._keys(capture, {'ledger_path', 'attempt_id'})
            ledger_name = relative(capture['ledger_path'])
            if ledger_name not in payloads:
                raise ValueError('primary capture ledger missing')
            ledger = strict_json(payloads[ledger_name])
            attempts = [row for row in ledger['attempts'] if row['id'] == capture['attempt_id']]
            if len(attempts) != 1:
                raise ValueError('unique primary capture attempt required')
            attempt = attempts[0]
            captured_path = (PurePosixPath(ledger_name).parent / attempt['path']).as_posix()
            if (captured_path != reference['body_path'] or attempt['url'] != reference['source_url']
                    or attempt['final_url'] != attempt['url'] or attempt['redirect_count'] != 0
                    or attempt['http_status'] != 200 or attempt['outcome'] != 'succeeded'
                    or attempt['curl_exit_code'] != 0 or attempt['tls_verification_result'] != 0
                    or attempt['sha256'] != runtime.digest(payloads[reference['body_path']])
                    or attempt['bytes'] != len(payloads[reference['body_path']])
                    or attempt['finished_at'] != reference['capture_finished_at']
                    or instant(attempt['started_at']) > instant(attempt['finished_at'])):
                raise ValueError('primary capture identity or chronology differs')
        elif any(reference[field] is not None for field in ('capture_finished_at', 'source_url', 'capture')):
            raise ValueError('screening note is not primary-source capture')
        spans = reference['support_spans']
        if not isinstance(spans, list) or not 1 <= len(spans) <= 20:
            raise ValueError('bounded exact support spans required')
        for span in spans:
            runtime._keys(span, {'path', 'start', 'end', 'sha256'})
            body = payloads.get(relative(span['path']))
            if (body is None or type(span['start']) is not int or type(span['end']) is not int
                    or not 0 <= span['start'] < span['end'] <= len(body)
                    or runtime.digest(body[span['start']:span['end']]) != span['sha256']):
                raise ValueError('exact support span differs')
    result = diagnostic.score(review['roster'], review['outcomes'])
    used = set()
    for case, outcome in zip(review['roster'], review['outcomes'], strict=True):
        for row in (case, outcome):
            ids = row['source_reference_ids']
            if any(key not in references for key in ids):
                raise ValueError('case references unavailable evidence')
            used.update(ids)
            if row['source_document_date'] is not None and not any(
                    references[key]['source_document_date'] == row['source_document_date'] for key in ids):
                raise ValueError('case date lacks its reviewed source reference')
            needs_primary = ((row is case and case['disposition'] == 'eligible')
                             or (row is outcome and outcome['outcome']['status'] != 'unknown'))
            if needs_primary:
                if not ids or any(references[key]['kind'] != 'reviewed_primary' for key in ids):
                    raise ValueError('eligible targets and substantive outcomes require primary-source review')
    if used != set(references):
        raise ValueError('unreferenced or missing source evidence')
    return {'review_sha256': expected_sha256, 'review': review, 'diagnostic': result,
            'verification_scope': 'Exact retained file and span bytes and reference membership; source semantics and PDF visual correspondence remain manually reviewed, not independently proven.',
            'verified_input_files': len(payloads), 'verified_input_bytes': sum(map(len, payloads.values()))}


def codes():
    current = {name: runtime.digest(runtime.read(ROOT / name)) for name in CODE_PATHS}
    if current != LOADED_CODES:
        raise ValueError('producer source changed after module loading')
    return current


def create(review_path, review_sha256, reference_root, output):
    started = now()
    result = load_review(review_path, review_sha256, reference_root)
    if instant(result['review']['reviewed_at']) > instant(started):
        raise ValueError('execution cannot precede the actual review')
    report = {'format': REPORT_FORMAT, 'execution_started_at': started,
              'execution_finished_at': now(), 'producer_files': codes(), 'result': result}
    raw = runtime.canonical(report)
    runtime.write_new(output, raw)
    return {'path': str(output), 'sha256': runtime.digest(raw),
            'denominators': result['diagnostic']['denominators']}


def verify(report_path, report_sha256, review_path, review_sha256, reference_root):
    runtime._hex(report_sha256, 64)
    raw = runtime.read(report_path)
    if runtime.digest(raw) != report_sha256:
        raise ValueError('trusted report hash mismatch')
    report = strict_json(raw)
    runtime._keys(report, {'format', 'execution_started_at', 'execution_finished_at', 'producer_files', 'result'})
    result = load_review(review_path, review_sha256, reference_root)
    if (report['format'] != REPORT_FORMAT or report['producer_files'] != codes()
            or report['result'] != result or runtime.canonical(report) != raw):
        raise ValueError('exact diagnostic reconstruction or producer differs')
    if not instant(result['review']['reviewed_at']) <= instant(report['execution_started_at']) <= instant(report['execution_finished_at']) <= instant(now()):
        raise ValueError('actual execution chronology differs')
    return {'verified': True, 'sha256': report_sha256,
            'scope': 'Exact retained-source diagnostic reconstruction, not a historical forecast replay.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', required=True)
    parser.add_argument('--review-sha256', required=True)
    parser.add_argument('--reference-root', required=True)
    sub = parser.add_subparsers(dest='operation', required=True)
    make = sub.add_parser('create')
    make.add_argument('--output', required=True)
    check = sub.add_parser('verify')
    check.add_argument('--report', required=True)
    check.add_argument('--report-sha256', required=True)
    args = parser.parse_args()
    if args.operation == 'create':
        result = create(args.review, args.review_sha256, args.reference_root, args.output)
    else:
        result = verify(args.report, args.report_sha256, args.review, args.review_sha256, args.reference_root)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
