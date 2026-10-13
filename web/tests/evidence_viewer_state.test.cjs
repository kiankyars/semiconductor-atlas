// Deterministic script/state tests with a minimal DOM double; not browser rendering QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../semiconductor_atlas/viewer/viewer.js'), 'utf8');
const cutoff = '2026-09-09T05:52:00.817388Z';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.value = ''; this._text = ''; }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(_) { throw new Error('Metadata must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(event, fn) { (this.listeners[event] ||= []).push(fn); }
  async fire(event) { for (const fn of this.listeners[event] || []) await fn({preventDefault() {}}); }
  focus() { this.focused = true; }
}
function descendants(root, tag) { return root.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...descendants(child, tag)]); }
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup() {
  const ids = {}, pending = [];
  const get = id => ids[id] ||= new Element(id);
  const document = {getElementById: get, createElement: tag => new Element(tag),
    querySelectorAll: selector => { assert.equal(selector, '#list button'); return descendants(get('list'), 'button'); }};
  const fetch = (url, options) => new Promise((resolve, reject) => pending.push({url, options, resolve, reject}));
  vm.runInNewContext(source, {document, fetch, URL, URLSearchParams, AbortController});
  function request(prefix) { const index = pending.findIndex(item => item.url.startsWith(prefix)); assert.notEqual(index, -1, prefix); return pending.splice(index, 1)[0]; }
  async function answer(req, value, ok = true) { req.resolve({ok, json: async () => value}); await tick(); }
  return {get, pending, request, answer};
}
function entity(id, name = 'Ohio One') { return {id, display_name: name, kind: 'project', stable_key: id,
  known_source_statement_count: 3, world_current_claim_count: 0, direct_sources: [{publisher: 'City'}],
  dossier_selection: {entity_id: id, as_of: '2026-09-09', recorded_at: cutoff}}; }
function page(results = [entity('city'), entity('nist')], changes = {}) { return {results, offset: 0, next_offset: null,
  returned_entities: results.length, total_matched_entities: results.length, visible_registry_entities: 4904,
  as_of: '2026-09-09', recorded_at: cutoff, ...changes}; }
function claim(changes = {}) { return {id: 'claim', predicate: 'source_reported.construction_pace',
  claim_kind: 'source_statement', confidence: null, value_kind: 'scalar', value: {value: 'slowing', unit: null},
  recorded_at: cutoff, superseded_at: null, valid_from: null, evidence: [], dependencies: [], ...changes}; }
function dossier(changes = {}) { return {entity: entity('city'), as_of: '2026-09-09', recorded_at: cutoff,
  source_statements: [claim()], world_current_claims: [], recorded_history: [claim()], dependency_context: [],
  ingestion_runs: [], evidence_summary: {source_family_count: 1, independent_corroboration: 'not_assessed'},
  boundaries: ['Not verified physical capacity'], ...changes}; }
async function loaded(report = page()) {
  const env = setup();
  await env.answer(env.request('api/config'), {as_of: '2026-09-09', recorded_at: cutoff,
    snapshot: 'Fixed read-only database transaction; restart to see later admissions', entity_kinds: ['project', 'facility']});
  const initial = env.request('api/search');
  const params = new URL(initial.url, 'http://local/').searchParams;
  assert.equal(params.get('recorded_at'), cutoff);
  await env.answer(initial, report);
  return env;
}
async function selected(report = dossier()) {
  const env = await loaded();
  const button = descendants(env.get('list'), 'button')[0];
  // The click listener starts a request; do not await its unresolved promise.
  const started = button.fire('click');
  const req = env.request('api/dossier');
  assert.equal(new URL(req.url, 'http://local/').searchParams.get('recorded_at'), cutoff);
  await env.answer(req, report); await started;
  return env;
}

test('bootstrap preserves exact microsecond strings and all entity kinds', async () => {
  const env = await loaded();
  assert.equal(env.get('recorded-at').value, cutoff);
  assert.match(env.get('snapshot').textContent, /restart/);
  assert.equal(env.get('kind').children.length, 2);
  assert.equal(env.get('search').disabled, false);
});
test('same names remain separate selectable IDs with full denominator', async () => {
  const env = await loaded();
  assert.equal(descendants(env.get('list'), 'button').length, 2);
  assert.match(env.get('list').textContent, /city/);
  assert.match(env.get('list').textContent, /nist/);
  assert.equal(env.get('counts').textContent, '2 matches / 4904 visible registry entries');
});
test('selection shows four semantic sections and visible compact values', async () => {
  const env = await selected();
  const summaries = descendants(env.get('dossier'), 'summary').map(e => e.textContent);
  for (const text of ['Source statements · 1', 'World-current claims · 0', 'Recorded history · 1', 'Dependency context · 0']) assert.ok(summaries.includes(text));
  assert.ok(summaries.some(text => text.includes('construction pace') && text.includes('slowing')));
  assert.match(env.get('dossier').textContent, /null — unknown or not applicable/);
  assert.doesNotMatch(env.get('dossier').textContent, /0%/);
  assert.equal(descendants(env.get('list'), 'button')[0].attributes['aria-pressed'], 'true');
  assert.equal(env.get('dossier').focused, true);
});

test('one-sided occurrence is never shown as an exact date or null-to-date interval', async () => {
  const bounded = claim({value_kind: 'milestone', value: {milestone_type: 'sample_production_start',
    status: 'completed', date_relation: 'on_or_before', date_low: null, date_base: null,
    date_high: '2026-07-13', date_precision: 'day', date_literal: 'Sample production has already commenced'}});
  const env = await selected(dossier({source_statements: [bounded], recorded_history: [bounded]}));
  const summaries = descendants(env.get('dossier'), 'summary').map(e => e.textContent).join('\n');
  assert.match(summaries, /occurred on or before 2026-07-13/);
  assert.match(summaries, /lower bound and exact date unknown/);
  assert.doesNotMatch(summaries, /null to|precision day|midpoint/);
});
test('malicious names and evidence labels stay text, only safe source links are active', async () => {
  const env = await selected(dossier({entity: entity('city', '<script>alert(1)</script>'),
    source_statements: [claim({evidence: [
      {document_url: 'javascript:alert(1)', publisher: '<img onerror=bad>', source_family: 'family', role: 'refute'},
      {document_url: 'https://user:password@example.test/', publisher: 'bad credentials'},
      {document_url: 'https://example.test/source', publisher: 'Publisher', source_family: 'family', role: 'support'}]})]}));
  const links = descendants(env.get('dossier'), 'a');
  assert.equal(links.length, 2); // One safe source and the local complete JSON endpoint.
  assert.equal(links[0].href, 'https://example.test/source');
  assert.equal(links[0].referrerPolicy, 'no-referrer');
  assert.equal(links[0].rel, 'noopener noreferrer');
  assert.match(env.get('dossier').textContent, /javascript:alert/);
  assert.equal(descendants(env.get('dossier'), 'script').length, 0);
});
test('rate denominators and fiscal area units retain their meaning', async () => {
  const rate = claim({predicate: 'source_rate.wafer_production_rate', value: {source_rate: {
    source_number_literal: '21,000', numerator_unit: 'native_wafers', denominator_unit: 'month',
    numeric_qualifier: 'as_reported_precision_unspecified', reference_period_kind: 'not_stated',
    scope_label: 'Senator-described facility', physical_capacity_basis: null}}});
  const area = claim({predicate: 'source_quantity.produced_wafer_area', value: {source_quantity: {
    source_number_literal: '128.4', source_unit_scale: 1000000, unit: 'square_centimeters',
    numeric_qualifier: 'as_reported_precision_unspecified', period_kind: 'fiscal_year',
    period_start: '2023-10-01', period_end: '2024-09-30', scope_label: 'Issuer-reported site', physical_capacity_basis: null}}});
  const env = await selected(dossier({source_statements: [rate, area]}));
  const summaries = descendants(env.get('dossier'), 'summary').map(e => e.textContent).join('\n');
  assert.match(summaries, /21,000 native_wafers\/month/);
  assert.match(summaries, /not physical capacity/);
  assert.match(summaries, /128.4 × 1000000 square_centimeters/);
  assert.match(summaries, /fiscal year 2023-10-01 to 2024-09-30/);
});
test('milestone bounds do not invent a midpoint', async () => {
  const env = await selected(dossier({source_statements: [claim({value_kind: 'milestone', value: {
    status: 'completed', milestone_type: 'commercial_production_commencement', date_low: '2024-01-01',
    date_high: '2024-12-31', date_base: null, date_precision: 'year'}})]}));
  assert.match(env.get('dossier').textContent, /2024-01-01 to 2024-12-31 · precision year · midpoint null/);
});
test('changing cutoff clears existing results and dossier until submission', async () => {
  const env = await selected(); env.get('recorded-at').value = '2026-09-09T05:52:00.817387Z';
  await env.get('filters').fire('input');
  assert.equal(env.get('list').children.length, 0);
  assert.equal(env.get('counts').textContent, '');
  assert.equal(env.get('next').disabled, true);
  assert.doesNotMatch(env.get('dossier').textContent, /slowing/);
  await env.get('filters').fire('submit');
  const req = env.request('api/search');
  assert.equal(new URL(req.url, 'http://local/').searchParams.get('recorded_at'), '2026-09-09T05:52:00.817387Z');
  await env.answer(req, page([]));
  assert.match(env.get('page').textContent, /No matches/);
});
test('old search response cannot overwrite a newer query', async () => {
  const env = await loaded(); env.get('query').value = 'Old'; await env.get('filters').fire('submit'); const old = env.request('api/search');
  env.get('query').value = 'New'; await env.get('filters').fire('submit'); const recent = env.request('api/search');
  assert.equal(old.options.signal.aborted, true);
  await env.answer(recent, page([entity('new', 'New entity')]));
  await env.answer(old, page([entity('old', 'Old entity')]));
  assert.match(env.get('list').textContent, /New entity/);
  assert.doesNotMatch(env.get('list').textContent, /Old entity/);
});
test('old dossier cannot overwrite later selection', async () => {
  const env = await loaded(); const buttons = descendants(env.get('list'), 'button');
  const one = buttons[0].fire('click'); const old = env.request('api/dossier');
  const two = buttons[1].fire('click'); const recent = env.request('api/dossier');
  await env.answer(recent, dossier({entity: entity('nist', 'NIST exact record')})); await two;
  await env.answer(old, dossier({entity: entity('city', 'City exact record')})); await one;
  assert.match(env.get('dossier').textContent, /NIST exact record/);
  assert.doesNotMatch(env.get('dossier').textContent, /City exact record/);
});
test('cutoff edit suppresses an already in-flight dossier', async () => {
  const env = await loaded(); const started = descendants(env.get('list'), 'button')[0].fire('click'); const old = env.request('api/dossier');
  await env.get('filters').fire('input'); await env.answer(old, dossier()); await started;
  assert.match(env.get('dossier').textContent, /Search inputs changed/);
  assert.doesNotMatch(env.get('dossier').textContent, /slowing/);
});
test('pagination carries applied query and clocks', async () => {
  const env = await loaded(page([entity('first')], {next_offset: 50, total_matched_entities: 51}));
  const started = env.get('next').fire('click'); const req = env.request('api/search');
  const params = new URL(req.url, 'http://local/').searchParams;
  assert.equal(params.get('offset'), '50'); assert.equal(params.get('recorded_at'), cutoff);
  await env.answer(req, page([entity('last')], {offset: 50, total_matched_entities: 51})); await started;
  assert.equal(env.get('previous').disabled, false); assert.equal(env.get('next').disabled, true);
  assert.equal(env.get('page').textContent, '51–51');
});
test('failed dossier is not presented as empty evidence', async () => {
  const env = await loaded(); const started = descendants(env.get('list'), 'button')[0].fire('click');
  await env.answer(env.request('api/dossier'), {error: 'Inconsistent lineage'}, false); await started;
  assert.match(env.get('dossier').textContent, /Dossier unavailable/);
  assert.match(env.get('dossier').textContent, /Inconsistent lineage/);
  assert.doesNotMatch(env.get('dossier').textContent, /No eligible records/);
});
test('failed search clears old results and leaves an explicit error', async () => {
  const env = await loaded(); await env.get('filters').fire('submit');
  await env.answer(env.request('api/search'), {error: 'Invalid cutoff'}, false);
  assert.equal(env.get('list').children.length, 0);
  assert.equal(env.get('status').textContent, 'Invalid cutoff');
  assert.equal(env.get('next').disabled, true);
});

function knowledgePage(changes = {}) {
  return {since: '2026-09-08T19:00:00Z', recorded_at: cutoff, as_of: '2026-09-09',
    events: [{event_id: 'evidence-event:stable', event_type: 'claim_admitted', event_at: cutoff,
      entity: entity('city'), claim: claim(), dossier_selection: entity('city').dossier_selection}],
    event_counts: {claim_admitted: 1, claim_superseded: 0}, distinct_claims: 1, distinct_subjects: 1,
    total_events: 1, returned_events: 1, offset: 0, next_offset: null, population_sha256: 'a'.repeat(64),
    page_validation_cutoff: cutoff, dependency_context: [], ingestion_runs: [], boundaries: ['Not physical events'], ...changes};
}
async function newEvidence(report = knowledgePage()) {
  const env = await loaded(); env.get('view-mode').value = 'changes'; env.get('since').value = report.since;
  await env.get('filters').fire('input'); await env.get('filters').fire('submit');
  await env.answer(env.request('api/changes'), report); return env;
}
test('new evidence mode hides literal search and sends exact start/end strings', async () => {
  const env = await loaded(); env.get('query').value = 'ignored registry query';
  env.get('view-mode').value = 'changes'; env.get('since').value = '2026-09-08T19:00:00.000001Z';
  await env.get('filters').fire('input'); await env.get('filters').fire('submit');
  const req = env.request('api/changes'), params = new URL(req.url, 'http://local/').searchParams;
  assert.equal(params.get('since'), '2026-09-08T19:00:00.000001Z'); assert.equal(params.get('recorded_at'), cutoff);
  assert.equal(params.has('query'), false); assert.equal(env.get('query').disabled, true);
  assert.equal(env.get('query-label').hidden, true); assert.equal(env.get('since').required, true);
  assert.equal(env.get('since-label').hidden, false); assert.equal(env.get('list-title').textContent, 'New evidence');
  await env.answer(req, knowledgePage());
  assert.match(env.get('counts').textContent, /1 recorded events · 1 claims · 1 exact subjects/);
  assert.match(env.get('list').textContent, /slowing/); assert.match(env.get('dossier').textContent, /not manufacturing events/);
});
test('supersession remains bookkeeping and old claim value stays inspectable', async () => {
  const report = knowledgePage(); report.events[0].event_type = 'claim_superseded';
  report.event_counts = {claim_admitted: 0, claim_superseded: 1};
  const env = await newEvidence(report);
  assert.match(env.get('list').textContent, /Bookkeeping only; not closure, cancellation or capacity loss/);
  assert.match(env.get('list').textContent, /evidence-event:stable/);
  assert.match(env.get('dossier').textContent, /0 admissions · 1 supersessions/);
});
test('event dossier drill-through retains the exact subject and cutoffs', async () => {
  const env = await newEvidence(), button = descendants(env.get('list'), 'button')[0];
  const started = button.fire('click'), req = env.request('api/dossier');
  const params = new URL(req.url, 'http://local/').searchParams;
  assert.equal(params.get('entity_id'), 'city'); assert.equal(params.get('recorded_at'), cutoff);
  await env.answer(req, dossier()); await started;
  assert.match(env.get('dossier').textContent, /Source statements · 1/);
});
test('evidence pagination binds the inventory hash in both directions', async () => {
  const env = await newEvidence(knowledgePage({next_offset: 50, total_events: 51}));
  let started = env.get('next').fire('click'), req = env.request('api/changes');
  let params = new URL(req.url, 'http://local/').searchParams;
  assert.equal(params.get('population_sha256'), 'a'.repeat(64)); assert.equal(params.get('offset'), '50');
  assert.equal(params.get('since'), '2026-09-08T19:00:00Z');
  await env.answer(req, knowledgePage({offset: 50, total_events: 51})); await started;
  started = env.get('previous').fire('click'); req = env.request('api/changes');
  params = new URL(req.url, 'http://local/').searchParams;
  assert.equal(params.get('population_sha256'), 'a'.repeat(64)); assert.equal(params.get('offset'), '0');
  await env.answer(req, knowledgePage({next_offset: 50, total_events: 51})); await started;
});
test('switching back to registry suppresses stale evidence response and drops since', async () => {
  const env = await newEvidence(); await env.get('filters').fire('submit'); const old = env.request('api/changes');
  env.get('view-mode').value = 'registry'; await env.get('filters').fire('input'); await env.get('filters').fire('submit');
  const recent = env.request('api/search'); assert.equal(new URL(recent.url, 'http://local/').searchParams.has('since'), false);
  await env.answer(recent, page([entity('new', 'New registry result')])); await env.answer(old, knowledgePage());
  assert.match(env.get('list').textContent, /New registry result/); assert.doesNotMatch(env.get('list').textContent, /slowing/);
  assert.equal(env.get('since').disabled, true); assert.equal(env.get('query-label').hidden, false);
});
test('changing since suppresses a stale dossier and clears applied population', async () => {
  const env = await newEvidence(); const started = descendants(env.get('list'), 'button')[0].fire('click');
  const old = env.request('api/dossier'); env.get('since').value = '2026-09-09T00:00:00Z';
  await env.get('filters').fire('input'); await env.answer(old, dossier()); await started;
  assert.doesNotMatch(env.get('dossier').textContent, /slowing/); assert.equal(env.get('next').disabled, true);
  await env.get('filters').fire('submit'); const req = env.request('api/changes');
  assert.equal(new URL(req.url, 'http://local/').searchParams.has('population_sha256'), false);
  await env.answer(req, knowledgePage());
});
test('empty knowledge window and failed lineage have distinct states', async () => {
  const env = await newEvidence(knowledgePage({events: [], total_events: 0, returned_events: 0,
    distinct_claims: 0, distinct_subjects: 0, event_counts: {claim_admitted: 0, claim_superseded: 0}}));
  assert.match(env.get('list').textContent, /does not establish no real-world change/);
  await env.get('filters').fire('submit'); await env.answer(env.request('api/changes'), {error: 'Invalid lineage'}, false);
  assert.equal(env.get('list').children.length, 0); assert.equal(env.get('counts').textContent, '');
  assert.match(env.get('status').textContent, /Invalid lineage/);
  assert.doesNotMatch(env.get('page').textContent, /No recorded events/);
});
