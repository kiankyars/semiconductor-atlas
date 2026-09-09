#!/usr/bin/env python3
"""Verify a source-bound outcome screen without changing its frozen PMT population.

Complete original event membership and targets are mechanically preserved. New
events at a known project remain explicitly supplemental and cannot become an
original target success. Evidence identity is verified; semantic adjudication,
physical truth, historical availability and forecast skill are not proven.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import evaluate_source_guidance as adapter
from scripts import frozen_runtime as runtime
from scripts import verify_pmt_population_review as population
from semiconductor_atlas import source_guidance_outcomes as diagnostic
from semiconductor_atlas.curated_capture import validate_capture

FORMAT = 'semiconductor-atlas-nist-pmt-outcome-screen-v1'
BOUNDARIES = dict.fromkeys(('historical_availability_verified', 'global_census',
    'independent_or_blind_evaluation', 'canonical_facility_identity',
    'physical_capacity_claims', 'calibration', 'model_training', 'database_modified',
    'prospective_study_modified', 'scheduled_runtime_modified', 'raw_redistribution'), False)
ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = tuple(sorted(set(adapter.CODE_PATHS) | {
    'scripts/verify_pmt_outcome_screen.py', 'scripts/verify_pmt_population_review.py'}))
LOADED_CODES = {name: runtime.digest(runtime.read(ROOT / name)) for name in CODE_PATHS}


def validate_cases(projects, roster, outcomes, supplemental, source_dates):
    """Preserve every original association, including groups with no timing target.

    ``projects`` are verified included-manufacturing population records;
    ``source_dates`` maps their original article positions to verified ISO dates.
    Source bytes/references are checked separately by :func:`verify`.
    """
    if not isinstance(projects, list) or not 1 <= len(projects) <= 1000:
        raise ValueError('bounded nonempty frozen manufacturing population required')
    by_project = {row['id']: row for row in projects}
    if len(by_project) != len(projects) or any(
            row['selection'] != 'included_manufacturing' for row in projects):
        raise ValueError('unique included manufacturing project identities required')
    expected = {}
    for project in projects:
        key, timing, pos = project['id'], project['timing'], project['position']
        if pos not in source_dates:
            raise ValueError('original article metadata date required')
        diagnostic._day(source_dates[pos])
        for ordinal, item in enumerate(timing or [None], 1):
            identifier = f'{key}--timing-{ordinal:02d}' if item else f'{key}--no-timing'
            target = item['target'] if item else None
            expected[identifier] = {'case_id': identifier, 'project_id': key,
                'project_group': key, 'scope_id': f'{key}--original-pmt-scope',
                'event_type': item['event'] if item else 'project_intervention_undated',
                'target': target, 'disposition': 'eligible' if target is not None else 'unresolved',
                'source_reference_ids': [f'nist2024-d{pos}'],
                'source_document_date': source_dates[pos]}
    if not isinstance(supplemental, list) or len(supplemental) > 100:
        raise ValueError('bounded explicit supplemental case array required')
    supplements = {}
    for item in supplemental:
        population.keys(item, {'case_id', 'project_id', 'relation', 'reason'})
        for field in ('case_id', 'project_id', 'reason'):
            population.text(item[field])
        key = item['case_id']
        if (key in expected or key in supplements or item['project_id'] not in by_project
                or item['relation'] != 'distinct_event_same_project'):
            raise ValueError('supplement must be a unique distinct event at a frozen project')
        supplements[key] = item
    result = diagnostic.score(roster, outcomes)
    actual = {row['case_id']: row for row in roster}
    if set(actual) != set(expected) | set(supplements):
        raise ValueError('complete original event cases plus only declared supplements required')
    for key, expected_row in expected.items():
        if any(actual[key][field] != value for field, value in expected_row.items()):
            raise ValueError('original event identity, scope, target, disposition or source changed')
    outcome_by_id = {row['case_id']: row for row in outcomes}
    supplemental_identities, supplemental_scopes = set(), set()
    for key, item in supplements.items():
        case, outcome = actual[key], outcome_by_id[key]
        project = item['project_id']
        original_events = {row['event_type'] for row in expected.values() if row['project_id'] == project}
        identity = (project, case['event_type'], case['scope_id'])
        scope = (project, case['scope_id'])
        if (case['project_id'] != project or case['project_group'] != project
                or case['event_type'] in original_events
                or case['scope_id'] == f'{project}--original-pmt-scope'
                or identity in supplemental_identities or scope in supplemental_scopes
                or case['target'] is not None or case['disposition'] != 'unresolved'
                or outcome['outcome']['status'] == 'unknown'
                or not outcome['source_reference_ids']):
            raise ValueError('supplement requires its own event/scope, no target, and substantive evidence')
        supplemental_identities.add(identity)
        supplemental_scopes.add(scope)
    original_rows = [row for row in result['cases'] if row['case']['case_id'] in expected]
    supplement_rows = [row for row in result['cases'] if row['case']['case_id'] in supplements]
    def counts(rows):
        return {'cases': len(rows),
            'project_groups': len({row['case']['project_group'] for row in rows}),
            'calendar_target_cases': sum(row['case']['target'] is not None for row in rows),
            'scored_cases': sum(row['target_met'] is not None for row in rows),
            'unknown_outcomes': sum(row['outcome_status'] == 'unknown' for row in rows),
            'outcome_status_counts': dict(sorted(Counter(row['outcome_status'] for row in rows).items()))}
    return {'manufacturing_project_groups': len(by_project), 'original': counts(original_rows),
            'supplemental': counts(supplement_rows), 'combined': result['denominators']}


def validate_primary_reference(reference, document, narrative):
    """Bind a reviewed primary reference to one frozen full article and capture."""
    expected = {'kind': 'reviewed_primary', 'body_path': document['body_path'],
        'source_document_date': document['source_document_date'],
        'source_url': document['url'], 'capture_finished_at': document['capture_finished_at'],
        'capture': {'ledger_path': document['source_checks_path'], 'attempt_id': document['attempt_id']},
        'support_spans': [{'path': document['body_path'], **narrative}]}
    if any(reference.get(key) != value for key, value in expected.items()):
        raise ValueError('primary reference must match frozen full article, metadata and capture')


def validate_input_binding(inventory, document, ledger_sha256):
    if (inventory.get(document['body_path']) != {'bytes': document['bytes'], 'sha256': document['sha256']}
            or inventory.get(document['source_checks_path'], {}).get('sha256') != ledger_sha256):
        raise ValueError('diagnostic inventory must preserve frozen article and capture ledger hashes')


def article_metadata(raw, captured_at):
    """Validate visible publication date against metadata without inventing issue time."""
    parsed = population.Metadata(raw)
    try:
        published = datetime.fromisoformat(parsed.values['article:published_time'])
        modified = datetime.fromisoformat(parsed.values['article:modified_time'])
        if published.tzinfo is None or modified.tzinfo is None:
            raise ValueError('timezone-qualified article metadata required')
        if not published <= modified <= adapter.instant(captured_at):
            raise ValueError('article metadata chronology differs')
        visible = [value for clock, value in parsed.times
                   if clock is not None and adapter.instant(clock) == published]
        if (len(visible) != 1 or datetime.strptime(visible[0], '%B %d, %Y').date()
                != published.date()):
            raise ValueError('visible and metadata publication dates differ')
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('article publication metadata, visible date and capture must agree') from error
    start, end = parsed.narratives[0]
    return published.date().isoformat(), {'start': start, 'end': end,
        'sha256': runtime.digest(raw[start:end])}, parsed.values


def verify(review_path, review_sha256, reference_root):
    runtime._hex(review_sha256, 64)
    root, payloads = Path(reference_root), {}
    def read(name, sha, size=None):
        adapter.relative(name)
        runtime._hex(sha, 64)
        if name not in payloads:
            if len(payloads) >= 100:
                raise ValueError('input file count bound exceeded')
            payloads[name] = runtime.read(root / name, limit=10_000_000)
        raw = payloads[name]
        if runtime.digest(raw) != sha or (size is not None and len(raw) != size):
            raise ValueError('retained input identity differs: ' + name)
        if sum(map(len, payloads.values())) > 50_000_000:
            raise ValueError('input byte bound exceeded')
        return raw
    def descriptor(row):
        population.keys(row, {'path', 'bytes', 'sha256'})
        if type(row['bytes']) is not int or not 0 < row['bytes'] <= 10_000_000:
            raise ValueError('bounded descriptor byte count required')
        return adapter.strict_json(read(row['path'], row['sha256'], row['bytes']))
    raw = runtime.read(review_path, limit=1_000_000)
    if runtime.digest(raw) != review_sha256:
        raise ValueError('trusted review hash mismatch')
    review = adapter.strict_json(raw)
    population.keys(review, {'format', 'reviewed_at', 'reviewed_by', 'selection', 'prior_exposure',
        'boundaries', 'population_review', 'diagnostic_review', 'later_capture_manifest', 'supplemental_cases'})
    if (review['format'] != FORMAT or review['boundaries'] != BOUNDARIES
            or any(value is not False for value in review['boundaries'].values())):
        raise ValueError('outcome screen boundaries differ')
    reviewed = adapter.instant(review['reviewed_at'])
    if reviewed > datetime.now(UTC):
        raise ValueError('review timestamp is in the future')
    for field in ('reviewed_by', 'selection', 'prior_exposure'):
        population.text(review[field])
    parent = descriptor(review['population_review'])
    parent_result = population.verify(root / review['population_review']['path'],
        review['population_review']['sha256'], root)
    reviewed_diagnostic = descriptor(review['diagnostic_review'])
    loaded = adapter.load_review(root / review['diagnostic_review']['path'],
        review['diagnostic_review']['sha256'], root)
    if loaded['review'] != reviewed_diagnostic:
        raise ValueError('diagnostic review changed during verification')
    diagnostic_clock = adapter.instant(reviewed_diagnostic['reviewed_at'])
    if not adapter.instant(parent['reviewed_at']) <= diagnostic_clock <= reviewed:
        raise ValueError('frozen population, diagnostic and wrapper review chronology differs')
    projects = [project for fragment in parent['fragments']
        for project in descriptor(fragment)['projects'] if project['selection'] == 'included_manufacturing']
    retention = descriptor(parent['retention_roster'])
    originals = retention['documents']
    parent_ledgers = {row['path'] + '/source_checks.json': row['source_checks_sha256']
                      for row in retention['capture_packets']}
    narratives = {row['displayed_index_position']: row['narrative_span']
                  for row in descriptor(parent['narratives'])['records']}
    dates = {row['position']: datetime.fromisoformat(row['article:published_time']).date().isoformat()
             for row in parent_result['source_metadata']}
    references = reviewed_diagnostic['source_references']
    required_positions = {row['position'] for row in projects}
    original_ids = {f'nist2024-d{pos}' for pos in required_positions}
    for document in originals:
        pos = document['displayed_index_position']
        if pos in required_positions:
            key = f'nist2024-d{pos}'
            if key not in references:
                raise ValueError('original primary reference missing')
            validate_primary_reference(references[key], {**document, 'source_document_date': dates[pos]},
                                       narratives[pos])
            validate_input_binding(reviewed_diagnostic['input_files'], document,
                                   parent_ledgers[document['source_checks_path']])
    manifest_name = review['later_capture_manifest']['path']
    if Path(manifest_name).name != 'manifest.json':
        raise ValueError('later capture manifest descriptor required')
    manifest = descriptor(review['later_capture_manifest'])
    capture_directory = Path(manifest_name).parent
    capture_root = root / capture_directory
    validate_capture(capture_root)
    if runtime.digest(runtime.read(capture_root / 'manifest.json', limit=10_000_000)) != review['later_capture_manifest']['sha256']:
        raise ValueError('later capture manifest changed during verification')
    ledger_name = (capture_directory / 'source_checks.json').as_posix()
    ledger_descriptor = manifest['files']['source_checks.json']
    ledger = adapter.strict_json(read(ledger_name, ledger_descriptor['sha256'], ledger_descriptor['bytes']))
    run_descriptor = manifest['files']['run.json']
    run = adapter.strict_json(read((capture_directory / 'run.json').as_posix(),
                                  run_descriptor['sha256'], run_descriptor['bytes']))
    if not adapter.instant(run['started_at']) <= adapter.instant(run['finished_at']) <= diagnostic_clock:
        raise ValueError('later capture must finish before diagnostic review')
    attempts = [row for row in ledger['attempts'] if row['purpose'] == 'source_document']
    if not 1 <= len(attempts) <= 30 or len({row['url'] for row in attempts}) != len(attempts):
        raise ValueError('bounded unique later source documents required')
    later_ids, metadata = set(), []
    for attempt in attempts:
        if (attempt['outcome'] != 'succeeded' or attempt['http_status'] != 200
                or attempt['final_url'] != attempt['url'] or attempt['redirect_count'] != 0
                or attempt['curl_exit_code'] != 0 or attempt['tls_verification_result'] != 0
                or not adapter.instant(attempt['started_at']) <= adapter.instant(attempt['finished_at']) <= diagnostic_clock):
            raise ValueError('later source capture identity or review chronology differs')
        name = (capture_directory / attempt['path']).as_posix()
        body = read(name, attempt['sha256'], attempt['bytes'])
        day, narrative, clocks = article_metadata(body, attempt['finished_at'])
        matches = [key for key, reference in references.items() if reference['body_path'] == name]
        if len(matches) != 1:
            raise ValueError('each later captured article requires exactly one primary reference')
        key = matches[0]
        if key in original_ids:
            raise ValueError('later reference cannot replace an original source identity')
        document = {'body_path': name, 'source_document_date': day,
            'url': attempt['url'], 'capture_finished_at': attempt['finished_at'],
            'source_checks_path': ledger_name, 'attempt_id': attempt['id'],
            'bytes': attempt['bytes'], 'sha256': attempt['sha256']}
        validate_primary_reference(references[key], document, narrative)
        validate_input_binding(reviewed_diagnostic['input_files'], document, ledger_descriptor['sha256'])
        later_ids.add(key)
        metadata.append({'source_reference_id': key, 'source_document_date': day,
            'capture_finished_at': attempt['finished_at'], **clocks})
    if set(references) != original_ids | later_ids:
        raise ValueError('references must equal frozen original articles and this later capture')
    counts = validate_cases(projects, reviewed_diagnostic['roster'], reviewed_diagnostic['outcomes'],
                            review['supplemental_cases'], dates)
    for supplemental in review['supplemental_cases']:
        key = supplemental['case_id']
        rows = [row for row in (*reviewed_diagnostic['roster'], *reviewed_diagnostic['outcomes'])
                if row['case_id'] == key]
        if any(not row['source_reference_ids'] or not set(row['source_reference_ids']) <= later_ids for row in rows):
            raise ValueError('supplemental event and outcome require this later primary evidence')
    current_codes = {name: runtime.digest(runtime.read(ROOT / name)) for name in CODE_PATHS}
    if current_codes != LOADED_CODES:
        raise ValueError('verifier source changed after module loading')
    return {'verified': True, 'review_sha256': review_sha256, 'denominators': counts,
        'parent_review_verification': parent_result, 'diagnostic_review_sha256': loaded['review_sha256'],
        'later_source_metadata': metadata, 'producer_files': current_codes,
        'verified_diagnostic_input_files': loaded['verified_input_files'],
        'verified_diagnostic_input_bytes': loaded['verified_input_bytes'],
        'scope': 'Complete frozen original-event membership, unchanged targets, separately scoped supplemental events, '
                 'and retained primary-source byte/capture/date bindings. Manual project/event correspondence and '
                 'source semantics are not independently adjudicated; no historical forecast replay, independent '
                 'physical measurement, forecast calibration or global census. Producer hashes identify this '
                 'verification but do not themselves archive an executable frozen runtime.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', required=True)
    parser.add_argument('--review-sha256', required=True)
    parser.add_argument('--reference-root', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.review, args.review_sha256, args.reference_root),
                     sort_keys=True, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
