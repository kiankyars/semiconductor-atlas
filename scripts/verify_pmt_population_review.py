#!/usr/bin/env python3
"""Read-only verification of a complete, manually reviewed NIST PMT population.

Checks retained bytes, full article membership, evidence allocation and date
contracts. It does not independently prove semantic adjudication, historical
availability, physical outcomes, or completeness outside the retained archive.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import frozen_runtime as runtime
from scripts.evaluate_source_guidance import instant, relative, strict_json
from semiconductor_atlas.ai_critical import _html_visible_text
from semiconductor_atlas.adapters import nist_discovery as index_parser
from semiconductor_atlas.source_guidance_outcomes import _target, _text

FORMAT = 'semiconductor-atlas-nist-pmt-population-review-v1'
BOUNDARIES = dict.fromkeys(('historical_availability_verified', 'global_census',
    'independent_or_blind_evaluation', 'canonical_facility_identity',
    'physical_capacity_claims', 'outcomes_adjudicated', 'forecast_scoring',
    'model_training', 'database_modified', 'scheduled_runtime_modified',
    'raw_redistribution'), False)
CLASSES = {'new_manufacturing_pmt', 'mixed_final_and_new_pmt',
           'final_award_only', 'research_only', 'other'}
PMT_CLASSES = {'new_manufacturing_pmt', 'mixed_final_and_new_pmt'}


def keys(value, fields):
    runtime._keys(value, fields)


def text(value):
    _text(value, 'review explanation')


def position(value):
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError('bounded integer document position required')
    return value


class Metadata(HTMLParser):
    def __init__(self, raw):
        super().__init__()
        self.values = {}
        self.source = raw.decode('utf-8')
        self.lines = [0]
        for line in self.source.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.depth, self.start, self.narratives = 0, None, []
        self.times, self.active_time, self.time_text = [], None, []
        self.feed(self.source)
        self.close()
        if self.depth or len(self.narratives) != 1:
            raise ValueError('exactly one complete article narrative required')

    def char_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'time':
            if self.active_time is not None:
                raise ValueError('nested article time element')
            self.active_time, self.time_text = attrs, []
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif 'text-with-summary' in attrs.get('class', '').split():
                self.depth, self.start = 1, self.char_offset()
        if tag == 'meta' and attrs.get('property') in (
                'article:published_time', 'article:modified_time'):
            key = attrs['property']
            if key in self.values:
                raise ValueError('duplicate source publication metadata')
            self.values[key] = attrs.get('content')

    def handle_endtag(self, tag):
        if tag == 'time' and self.active_time is not None:
            self.times.append((self.active_time.get('datetime'), ' '.join(''.join(self.time_text).split())))
            self.active_time, self.time_text = None, []
        if tag == 'div' and self.depth:
            self.depth -= 1
            if not self.depth:
                start, end = self.start, self.char_offset() + len('</div>')
                self.narratives.append((len(self.source[:start].encode('utf-8')),
                                       len(self.source[:end].encode('utf-8'))))

    def handle_data(self, value):
        if self.active_time is not None:
            self.time_text.append(value)


def verify_index(index, read):
    """Reproduce the complete current archive before selecting its 2024 rows."""
    pages, records = index['pages'], index['records']
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 20
            or not isinstance(records, list) or not 1 <= len(records) <= 1000):
        raise ValueError('bounded complete index pages and records required')
    if [row['displayed_position'] for row in records] != list(range(1, len(records) + 1)):
        raise ValueError('complete contiguous index positions required')
    if len({row['url'] for row in records}) != len(records):
        raise ValueError('unique index article URLs required')
    total, visited = None, []
    for page_number, page in enumerate(pages):
        rows = [row for row in records if row['source_url'] == page['url']]
        if not rows or page['page'] != page_number:
            raise ValueError('complete ordered index page inventory required')
        if len({(row['source_body_path'], row['source_body_sha256']) for row in rows}) != 1:
            raise ValueError('index page source identity conflict')
        raw = read(rows[0]['source_body_path'], rows[0]['source_body_sha256'])
        tree = index_parser._Tree()
        tree.feed(raw.decode('utf-8'))
        tree.close()
        nodes = list(tree.root.descendants())
        if [node.text() for node in nodes if node.tag == 'h1'] != ['Archived News']:
            raise ValueError('retained archive page identity differs')
        ranges = re.findall(r'Displaying (\d+) - (\d+) of (\d+)', tree.root.text())
        if len(ranges) != 1:
            raise ValueError('unique archive displayed range required')
        low, high, count = map(int, ranges[0])
        if total is None:
            total = count
        if (count != total or low != len(visited) + 1 or high < low or high > total
                or len(rows) != high - low + 1
                or (low, high, count) != (page['displayed_low'], page['displayed_high'], page['displayed_total'])
                or page['records'] != len(rows)):
            raise ValueError('archive page and complete population denominator differ')
        articles = [node for node in nodes if node.tag == 'article' and node.has_class('nist-teaser')]
        spans = list(re.finditer(rb'<article\b.*?</article>', raw, re.S))
        if len(articles) != len(rows) or len(spans) != len(rows):
            raise ValueError('every retained index article must be inventoried')
        for offset, (row, article, source_span) in enumerate(zip(rows, articles, spans, strict=True)):
            extracted = index_parser._news(article)
            date_node = index_parser._field(article, 'nist-teaser__date')
            visible = index_parser._one([node for node in date_node.descendants() if node.tag == 'time'], 'date').text()
            candidate = bool(re.search(r'preliminary\s+(?:terms|memorandum)', row['title'], re.I))
            if (any(row.get(key) != value for key, value in extracted.items())
                    or row['displayed_position'] != low + offset
                    or row['visible_index_date'] != visible
                    or datetime.strptime(visible, '%B %d, %Y').date().isoformat() != row['index_date'][:10]
                    or row['pmt_title_review_candidate'] != (row['calendar_2024'] and candidate)
                    or row['article_span'] != {'start': source_span.start(), 'end': source_span.end(),
                                              'sha256': runtime.digest(source_span.group())}):
                raise ValueError('index article identity, visible date or exact span differs')
            visited.append(row['displayed_position'])
        pager = index_parser._one([node for node in nodes if node.tag == 'nav' and node.has_class('pager')], 'pager')
        if (page['observed_pager_links'] != [node.attrs.get('href') for node in pager.descendants() if node.tag == 'a']
                or page['calendar_2024_records'] != sum(row['calendar_2024'] for row in rows)):
            raise ValueError('index pagination or per-page denominator differs')
    dates = [row['index_date'] for row in records]
    n2024 = sum(row['calendar_2024'] for row in records)
    expected_counts = {'all_index_rows': len(records), 'calendar_2024_rows': n2024,
        'outside_calendar_2024': len(records) - n2024,
        'pmt_title_review_candidates': sum(row['pmt_title_review_candidate'] for row in records),
        'body_reviewed_articles': 0, 'scored_cases': 0}
    if (visited != list(range(1, total + 1)) or len(records) != total
            or dates != sorted(dates, reverse=True) or index['denominators'] != expected_counts):
        raise ValueError('complete archive inventory or declared denominators differ')


def evidence(ids, pos, blocks):
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 30
            or any(not isinstance(key, str) for key in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('nonempty unique bounded evidence IDs required')
    if any(key not in blocks or not key.startswith(f'd{pos}-b') for key in ids):
        raise ValueError('evidence must belong to this exact article')
    return ' '.join(blocks[key]['text'] for key in ids)


def span(raw, row, *, bounds=None):
    keys(row, {'start', 'end', 'sha256'})
    start, end = row['start'], row['end']
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(raw)
            or (bounds and not bounds[0] <= start < end <= bounds[1])
            or runtime.digest(raw[start:end]) != row['sha256']):
        raise ValueError('retained source span differs')


def validate_decisions(articles, projects, positions, blocks):
    if not isinstance(articles, list) or len(articles) != len(positions):
        raise ValueError('complete article decision denominator required')
    seen = []
    classes = {}
    for row in articles:
        keys(row, {'position', 'classification', 'reason', 'evidence', 'non_target_dates'})
        pos = position(row['position'])
        seen.append(pos)
        if row['classification'] not in CLASSES:
            raise ValueError('explicit article classification required')
        classes[pos] = row['classification']
        text(row['reason'])
        evidence(row['evidence'], pos, blocks)
        if not isinstance(row['non_target_dates'], list):
            raise ValueError('non-target date decisions require an array')
        for explanation in row['non_target_dates']:
            text(explanation)
    if seen != positions or len(set(seen)) != len(seen):
        raise ValueError('article decisions must equal the complete ordered source population')
    if not isinstance(projects, list) or len(projects) > 1000:
        raise ValueError('bounded project population required')
    ids, project_positions, by_article = set(), [], Counter()
    for project in projects:
        keys(project, {'id', 'position', 'organization', 'location', 'scope', 'selection',
            'selection_reason', 'components', 'evidence', 'conditions', 'timing'})
        pos = position(project['position'])
        if pos not in classes or classes[pos] not in PMT_CLASSES:
            raise ValueError('project requires a newly announced PMT article')
        project_positions.append(pos)
        identifier = project['id']
        if (not isinstance(identifier, str)
                or not re.fullmatch(rf'nist2024-d{pos}-[a-z0-9-]+', identifier)
                or identifier in ids):
            raise ValueError('unique source-native project ID required')
        ids.add(identifier)
        for field in ('organization', 'location', 'scope', 'selection_reason'):
            text(project[field])
        if project['selection'] not in {'included_manufacturing', 'excluded_research'}:
            raise ValueError('explicit project inclusion or research exclusion required')
        if project['selection'] == 'included_manufacturing':
            by_article[pos] += 1
        evidence(project['evidence'], pos, blocks)
        components = project['components']
        if not isinstance(components, list) or not 1 <= len(components) <= 30:
            raise ValueError('explicit source-native components required')
        for component in components:
            keys(component, {'description', 'count'})
            text(component['description'])
            count = component['count']
            if count is not None and (type(count) is not int or not 1 <= count <= 100):
                raise ValueError('component count must be positive or explicitly unknown')
        conditions = project['conditions']
        included = project['selection'] == 'included_manufacturing'
        if not isinstance(conditions, list) or len(conditions) > 30 or (included and not conditions):
            raise ValueError('nonbinding funding conditions must be retained')
        for condition in conditions:
            keys(condition, {'kind', 'interpretation', 'evidence'})
            if condition['kind'] not in {'funding_general', 'funding_specific', 'project_specific'}:
                raise ValueError('funding and project conditions must remain distinct')
            text(condition['interpretation'])
            evidence(condition['evidence'], pos, blocks)
        if included and not any(row['kind'] == 'funding_general' for row in conditions):
            raise ValueError('general nonbinding PMT conditions required')
        timing = project['timing']
        if not isinstance(timing, list) or len(timing) > 30:
            raise ValueError('bounded timing statements required')
        for event in timing:
            keys(event, {'event', 'literal', 'classification', 'target', 'reason', 'evidence'})
            for field in ('event', 'literal', 'reason'):
                text(event[field])
            source = evidence(event['evidence'], pos, blocks)
            if ' '.join(event['literal'].split()) not in ' '.join(source.split()):
                raise ValueError('timing literal must occur in its exact supporting article blocks')
            kind, _, _ = _target(event['target'])
            expected = {'calendar_interval': 'interval', 'calendar_deadline': 'deadline',
                        'vague': None, 'relative': None, 'undated': None}
            if event['classification'] not in expected or expected[event['classification']] != kind:
                raise ValueError('literal precision and normalized target differ')
            if project['selection'] == 'excluded_research' and event['target'] is not None:
                raise ValueError('research scope cannot enter manufacturing timing targets')
    if project_positions != sorted(project_positions):
        raise ValueError('project articles must remain ordered')
    if set(by_article) != {pos for pos, classification in classes.items() if classification in PMT_CLASSES}:
        raise ValueError('every new PMT article requires its manufacturing project inventory')
    included = [row for row in projects if row['selection'] == 'included_manufacturing']
    timing = [event for row in included for event in row['timing']]
    distinct = {}
    for project in included:
        for event in project['timing']:
            key = (project['position'], event['event'], event['literal'], tuple(sorted(event['evidence'])))
            value = (event['classification'], event['target'])
            if key in distinct and distinct[key] != value:
                raise ValueError('shared source timing statement has conflicting interpretations')
            distinct[key] = value
    return {'article_classifications': dict(sorted(Counter(classes.values()).items())),
        'articles': len(articles), 'new_pmt_bearing_articles': len(by_article),
        'source_native_scope_groups': len(projects),
        'included_manufacturing_groups': len(included),
        'excluded_research_groups': len(projects) - len(included),
        'manufacturing_groups_without_timing_assertions': sum(not row['timing'] for row in included),
        'manufacturing_groups_with_calendar_targets': sum(any(event['target'] is not None
            for event in row['timing']) for row in included),
        'manufacturing_project_timing_associations': len(timing),
        'timing_association_classifications': dict(sorted(Counter(row['classification'] for row in timing).items())),
        'distinct_evidence_bound_timing_statements': len(distinct),
        'distinct_timing_classifications': dict(sorted(Counter(value[0] for value in distinct.values()).items())),
        'scored_cases': 0}


def verify(review_path, review_sha256, reference_root):
    runtime._hex(review_sha256, 64)
    review_raw = runtime.read(review_path, limit=1_000_000)
    if runtime.digest(review_raw) != review_sha256:
        raise ValueError('trusted review hash mismatch')
    review = strict_json(review_raw)
    keys(review, {'format', 'reviewed_at', 'reviewed_by', 'selection', 'prior_exposure',
        'rights', 'boundaries', 'index_roster', 'retention_roster', 'narratives', 'fragments'})
    if (review['format'] != FORMAT or review['boundaries'] != BOUNDARIES
            or any(value is not False for value in review['boundaries'].values())):
        raise ValueError('population review boundaries differ')
    reviewed = instant(review['reviewed_at'])
    if reviewed > datetime.now(UTC):
        raise ValueError('review timestamp is in the future')
    for field in ('reviewed_by', 'selection', 'prior_exposure', 'rights'):
        text(review[field])
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
        keys(row, {'path', 'sha256', 'bytes'})
        if type(row['bytes']) is not int or not 0 < row['bytes'] <= 10_000_000:
            raise ValueError('bounded descriptor byte count required')
        return strict_json(read(row['path'], row['sha256'], row['bytes']))

    index = descriptor(review['index_roster'])
    roster = descriptor(review['retention_roster'])
    narratives = descriptor(review['narratives'])
    if any(type(row['calendar_2024']) is not bool or row['calendar_2024'] !=
           (instant(row['index_date']).year == 2024) for row in index['records']):
        raise ValueError('calendar-2024 source selection differs')
    verify_index(index, read)
    indexed = [row for row in index['records'] if row['calendar_2024']]
    expected = [position(row['displayed_position']) for row in indexed]
    if not expected or expected != sorted(set(expected)):
        raise ValueError('unique ordered archive positions required')
    documents = roster['documents']
    records = narratives['records']
    if (len(documents) != len(indexed) or len(records) != len(documents)
            or narratives['source_roster_sha256'] != review['retention_roster']['sha256']):
        raise ValueError('complete source and narrative populations must correspond')
    ledgers = {}
    for packet in roster['capture_packets']:
        name = packet['path'] + '/source_checks.json'
        ledgers[name] = strict_json(read(name, packet['source_checks_sha256']))
    blocks, metadata, urls = {}, [], set()
    for index_row, document, record in zip(indexed, documents, records, strict=True):
        pos = position(document['displayed_index_position'])
        if (pos != index_row['displayed_position'] or document['url'] != index_row['url']
                or document['index_date'] != index_row['index_date']
                or document['index_title'] != index_row['title']
                or any(record.get(key) != value for key, value in document.items())):
            raise ValueError('index, article and narrative source identity differ')
        if document['url'] in urls:
            raise ValueError('duplicate article URL')
        urls.add(document['url'])
        raw = read(document['body_path'], document['sha256'], document['bytes'])
        attempts = [row for row in ledgers[document['source_checks_path']]['attempts']
                    if row['id'] == document['attempt_id']]
        if len(attempts) != 1:
            raise ValueError('unique capture attempt required')
        attempt = attempts[0]
        captured = (PurePosixPath(document['source_checks_path']).parent / attempt['path']).as_posix()
        if (captured != document['body_path'] or attempt['url'] != document['url']
                or attempt['final_url'] != document['url'] or attempt['http_status'] != 200
                or attempt['outcome'] != 'succeeded' or attempt['redirect_count'] != 0
                or attempt['curl_exit_code'] != 0 or attempt['tls_verification_result'] != 0
                or attempt['sha256'] != document['sha256'] or attempt['bytes'] != len(raw)
                or attempt['started_at'] != document['capture_started_at']
                or attempt['finished_at'] != document['capture_finished_at']
                or not instant(attempt['started_at']) <= instant(attempt['finished_at']) <= reviewed):
            raise ValueError('capture identity or actual review chronology differs')
        narrative = record['narrative_span']
        span(raw, narrative)
        parsed = Metadata(raw)
        try:
            published = datetime.fromisoformat(parsed.values['article:published_time'])
            modified = datetime.fromisoformat(parsed.values['article:modified_time'])
            if published.tzinfo is None or modified.tzinfo is None:
                raise ValueError('timezone missing')
            visible_times = [visible for clock, visible in parsed.times if clock == document['index_date']]
            visible_day = datetime.strptime(visible_times[0], '%B %d, %Y').date().isoformat()
            if (len(visible_times) != 1 or visible_day != document['index_date'][:10]
                    or published.date().isoformat() != visible_day):
                raise ValueError('publication date disagreement')
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError('article publication metadata, visible date and index date must agree') from error
        if parsed.narratives != [(narrative['start'], narrative['end'])]:
            raise ValueError('retained narrative must cover the complete source article')
        markup = raw[narrative['start']:narrative['end']]
        expected_blocks = [(narrative['start'] + match.start(), narrative['start'] + match.end())
                           for match in re.finditer(rb'<(p|li|h[2-6]|table)\b[^>]*>.*?</\1>', markup, re.S)]
        if [(row['start'], row['end']) for row in record['blocks']] != expected_blocks:
            raise ValueError('complete narrative block inventory differs')
        visible = _html_visible_text(raw[narrative['start']:narrative['end']].decode('utf-8'))
        if record['full_narrative_text'] != visible:
            raise ValueError('full narrative text differs from retained markup')
        for ordinal, block in enumerate(record['blocks'], 1):
            keys(block, {'id', 'start', 'end', 'sha256', 'text'})
            if block['id'] != f'd{pos}-b{ordinal:03}' or block['id'] in blocks:
                raise ValueError('source block identity differs')
            span(raw, {key: block[key] for key in ('start', 'end', 'sha256')},
                 bounds=(narrative['start'], narrative['end']))
            if block['text'] != _html_visible_text(raw[block['start']:block['end']].decode('utf-8')):
                raise ValueError('source block text differs from retained markup')
            blocks[block['id']] = block
        metadata.append({'position': pos, **parsed.values})
    fragments = review['fragments']
    if not isinstance(fragments, list) or not 1 <= len(fragments) <= 10:
        raise ValueError('bounded manual review fragments required')
    if len({row['path'] for row in fragments}) != len(fragments):
        raise ValueError('duplicate review fragment')
    articles, projects = [], []
    for item in fragments:
        fragment = descriptor(item)
        keys(fragment, {'articles', 'projects'})
        articles.extend(fragment['articles'])
        projects.extend(fragment['projects'])
    counts = validate_decisions(articles, projects, expected, blocks)
    return {'verified': True, 'review_sha256': review_sha256, 'denominators': counts,
        'source_metadata': metadata, 'verified_files': len(payloads),
        'verified_bytes': sum(map(len, payloads.values())), 'verified_blocks': len(blocks),
        'scope': 'Retained-byte and complete-article membership verification; project semantics, exclusions and date normalization remain manual review, not independent evidence or historical-availability proof.'}


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
