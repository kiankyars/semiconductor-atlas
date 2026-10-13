#!/usr/bin/env python3
"""Replay a complete later-archive review against a frozen PMT population.

This verifies retained evidence and explicit manual correspondence decisions.
It does not infer physical outcomes, score forecasts, or prove search recall.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import frozen_runtime as runtime
from scripts import verify_pmt_population_review as population
from scripts.evaluate_source_guidance import instant, relative, strict_json
from semiconductor_atlas.curated_capture import validate_capture

FORMAT = 'semiconductor-atlas-nist-followup-review-v1'
CLASSES = {'governance', 'research', 'new_pmt', 'final_award',
           'final_awards_and_additional_scope'}
RELATIONS = {'funding_finalized', 'additional_scope', 'distinct_project_same_company'}
BOUNDARIES = dict.fromkeys(('historical_availability_verified', 'global_census',
    'independent_or_blind_evaluation', 'canonical_facility_identity',
    'physical_outcomes_adjudicated', 'physical_capacity_claims', 'forecast_scoring',
    'database_modified', 'scheduled_runtime_modified', 'raw_redistribution'), False)


def validate_decisions(documents, positions, projects, blocks, parent_blocks):
    if not isinstance(documents, list) or not 1 <= len(documents) <= 1000:
        raise ValueError('bounded complete article decisions required')
    if [row['position'] for row in documents] != positions:
        raise ValueError('decisions must cover the complete ordered later archive selection')
    project_by_id = {row['id']: row for row in projects}
    if len(project_by_id) != len(projects):
        raise ValueError('duplicate frozen project identity')
    screens = {key: [] for key in project_by_id}
    for row in documents:
        population.keys(row, {'position', 'body', 'narrative_span', 'classification',
                              'reason', 'evidence', 'links'})
        pos = population.position(row['position'])
        if row['classification'] not in CLASSES:
            raise ValueError('explicit permitted article classification required')
        population.text(row['reason'])
        population.evidence(row['evidence'], pos, blocks)
        if not isinstance(row['links'], list) or len(row['links']) > len(projects):
            raise ValueError('bounded explicit project links required')
        seen = set()
        for link in row['links']:
            population.keys(link, {'project_id', 'relation', 'reason', 'evidence', 'parent_evidence'})
            key, relation = link['project_id'], link['relation']
            if key not in project_by_id or key in seen or relation not in RELATIONS:
                raise ValueError('unique known manufacturing project and permitted relation required')
            seen.add(key)
            if (relation == 'funding_finalized' and row['classification'] not in
                    {'final_award', 'final_awards_and_additional_scope'}):
                raise ValueError('funding finalization requires a final-award article')
            if relation == 'additional_scope' and row['classification'] != 'final_awards_and_additional_scope':
                raise ValueError('additional scope requires an explicitly classified scope amendment')
            population.text(link['reason'])
            population.evidence(link['evidence'], pos, blocks)
            population.evidence(link['parent_evidence'], project_by_id[key]['position'], parent_blocks)
            screens[key].append({'position': pos, 'relation': relation})
    counts = Counter(row['classification'] for row in documents)
    relations = Counter(link['relation'] for row in documents for link in row['links'])
    return {'articles': len(documents), 'article_classifications': dict(sorted(counts.items())),
            'manufacturing_groups': len(projects), 'project_link_relations': dict(sorted(relations.items())),
            'groups_with_finalized_funding_link': sum(any(link['relation'] == 'funding_finalized'
                for link in links) for links in screens.values()),
            'groups_without_finalized_funding_link_in_this_source_set': sum(not any(
                link['relation'] == 'funding_finalized' for link in links) for links in screens.values()),
            'physical_outcomes_adjudicated': 0, 'forecast_scores': 0}, screens


def verify(review_path, review_sha256, reference_root):
    root, payloads = Path(reference_root), {}

    def read(name, sha, size=None):
        relative(name)
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
        population.keys(row, {'path', 'sha256', 'bytes'})
        if type(row['bytes']) is not int or not 0 < row['bytes'] <= 10_000_000:
            raise ValueError('bounded descriptor byte count required')
        return read(row['path'], row['sha256'], row['bytes'])

    raw = runtime.read(Path(review_path), limit=10_000_000)
    if runtime.digest(raw) != review_sha256:
        raise ValueError('review identity differs')
    review = strict_json(raw)
    population.keys(review, {'format', 'reviewed_at', 'reviewed_by', 'selection',
        'prior_exposure', 'boundaries', 'population_review', 'capture_manifest', 'documents'})
    if review['format'] != FORMAT or review['boundaries'] != BOUNDARIES or any(
            value is not False for value in review['boundaries'].values()):
        raise ValueError('follow-up review boundaries differ')
    reviewed = instant(review['reviewed_at'])
    if reviewed > datetime.now(UTC):
        raise ValueError('review timestamp is in the future')
    for key in ('reviewed_by', 'selection', 'prior_exposure'):
        population.text(review[key])
    parent = strict_json(descriptor(review['population_review']))
    parent_result = population.verify(root / review['population_review']['path'],
        review['population_review']['sha256'], root)
    if instant(parent['reviewed_at']) > reviewed:
        raise ValueError('follow-up cannot precede the frozen population review')
    index = strict_json(descriptor(parent['index_roster']))
    selected = [row for row in index['records'] if instant(row['index_date']).year == 2025]
    positions = [row['displayed_position'] for row in selected]
    projects = [project for fragment in parent['fragments']
        for project in strict_json(descriptor(fragment))['projects']
        if project['selection'] == 'included_manufacturing']
    parent_narratives = strict_json(descriptor(parent['narratives']))
    parent_blocks = {block['id']: block for row in parent_narratives['records'] for block in row['blocks']}
    manifest_name = review['capture_manifest']['path']
    if Path(manifest_name).name != 'manifest.json':
        raise ValueError('capture manifest descriptor required')
    manifest = strict_json(descriptor(review['capture_manifest']))
    capture_root = root / Path(manifest_name).parent
    validate_capture(capture_root)
    if runtime.digest(runtime.read(capture_root / 'manifest.json', limit=10_000_000)) != review['capture_manifest']['sha256']:
        raise ValueError('capture manifest changed during validation')
    ledger_row = manifest['files']['source_checks.json']
    ledger = strict_json(read((Path(manifest_name).parent / 'source_checks.json').as_posix(),
                             ledger_row['sha256'], ledger_row['bytes']))
    attempts = [row for row in ledger['attempts'] if row['purpose'] == 'source_document']
    if len(attempts) != len(selected) or {row['url'] for row in attempts} != {row['url'] for row in selected}:
        raise ValueError('capture must equal all calendar-2025 rows in the frozen index')
    if not isinstance(review['documents'], list) or len(review['documents']) != len(selected):
        raise ValueError('complete later document population required')
    blocks, metadata = {}, []
    for index_row, row in zip(selected, review['documents'], strict=True):
        pos = index_row['displayed_position']
        if row['position'] != pos:
            raise ValueError('ordered later-source membership differs')
        matches = [attempt for attempt in attempts if attempt['url'] == index_row['url']]
        if len(matches) != 1:
            raise ValueError('unique source capture required')
        attempt = matches[0]
        expected_body = {'path': (Path(manifest_name).parent / attempt['path']).as_posix(),
                         'sha256': attempt['sha256'], 'bytes': attempt['bytes']}
        if (row['body'] != expected_body or attempt['outcome'] != 'succeeded'
                or attempt['http_status'] != 200 or attempt['final_url'] != index_row['url']
                or attempt['redirect_count'] != 0
                or not instant(attempt['started_at']) <= instant(attempt['finished_at']) <= reviewed):
            raise ValueError('article capture identity or chronology differs')
        body = descriptor(row['body'])
        parsed = population.Metadata(body)
        narrative = row['narrative_span']
        population.span(body, narrative)
        if parsed.narratives != [(narrative['start'], narrative['end'])]:
            raise ValueError('complete article narrative required')
        published = datetime.fromisoformat(parsed.values['article:published_time'])
        modified = datetime.fromisoformat(parsed.values['article:modified_time'])
        if published.tzinfo is None or modified.tzinfo is None:
            raise ValueError('timezone-qualified source metadata required')
        visible = [text for clock, text in parsed.times if clock == index_row['index_date']]
        if (len(visible) != 1 or datetime.strptime(visible[0], '%B %d, %Y').date().isoformat()
                != index_row['index_date'][:10] or published.date().isoformat() != index_row['index_date'][:10]
                or modified < published or modified > instant(attempt['finished_at'])):
            raise ValueError('article publication metadata and index date differ')
        markup = body[narrative['start']:narrative['end']]
        for ordinal, match in enumerate(re.finditer(rb'<(p|li|h[2-6]|table)\b[^>]*>.*?</\1>', markup, re.S), 1):
            identifier = f'd{pos}-b{ordinal:03}'
            blocks[identifier] = {'id': identifier, 'start': narrative['start'] + match.start(),
                'end': narrative['start'] + match.end(), 'sha256': runtime.digest(match.group()),
                'text': population._html_visible_text(match.group().decode('utf-8'))}
        metadata.append({'position': pos, 'url': index_row['url'], **parsed.values,
                         'capture_finished_at': attempt['finished_at']})
    counts, screens = validate_decisions(review['documents'], positions, projects, blocks, parent_blocks)
    return {'verified': True, 'review_sha256': review_sha256, 'denominators': counts,
            'parent_review_verification': parent_result, 'source_metadata': metadata,
            'project_screen': [{'project_id': key, 'links': links} for key, links in screens.items()],
            'verified_followup_blocks': len(blocks),
            'scope': 'Complete retained calendar-2025 index subset and manual correspondence review. '
                     'Empty links are no matching decision in this source set, not evidence of nonoccurrence. '
                     'The filtered index is not a complete later-news census; semantic review is not independently verified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', required=True)
    parser.add_argument('--review-sha256', required=True)
    parser.add_argument('--reference-root', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.review, args.review_sha256, args.reference_root),
                     ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
