"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, text, className) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; };
  const human = value => String(value).replaceAll("_", " ");
  const display = value => value === null ? "null — unknown or not applicable" : typeof value === "object" ? JSON.stringify(value) : String(value);
  const state = {generation: 0, selection: 0, request: null, page: null, query: null, mode: "registry"};
  async function get(route, params, signal) {
    const response = await fetch(route + (params ? "?" + new URLSearchParams(params) : ""), {signal, cache: "no-store", credentials: "omit"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Evidence request failed");
    return data;
  }
  function fields(object) {
    const table = node("table"), body = node("tbody");
    function walk(value, prefix = "") { for (const [key, item] of Object.entries(value)) {
      if (item && typeof item === "object" && !Array.isArray(item)) walk(item, prefix + key + ".");
      else { const row = node("tr"), heading = node("th", prefix + key); heading.scope = "row"; row.append(heading, node("td", display(item))); body.append(row); }
    } }
    walk(object); table.append(body); return table;
  }
  function disclosure(title, content, open = false) {
    const box = node("details"); box.open = open; box.append(node("summary", title), content); return box;
  }
  function safeLink(url) {
    try { const parsed = new URL(url); if (["https:", "http:"].includes(parsed.protocol) && !parsed.username && !parsed.password) {
      const link = node("a", url); link.href = parsed.href; link.target = "_blank"; link.rel = "noopener noreferrer"; link.referrerPolicy = "no-referrer"; return link;
    } } catch (_) { /* Unsupported links remain inert text. */ }
    return node("span", display(url));
  }
  function valueSummary(claim) {
    const v = claim.value;
    if (v.source_rate) { const r = v.source_rate; return `${r.source_number_literal} ${r.numerator_unit}/${r.denominator_unit} · ${human(r.numeric_qualifier)} · ${human(r.reference_period_kind)} · ${r.scope_label} · not physical capacity`; }
    if (v.source_quantity) { const q = v.source_quantity; return `${q.source_number_literal} × ${q.source_unit_scale} ${q.unit} · ${human(q.numeric_qualifier)} · ${human(q.period_kind)} ${q.period_start} to ${q.period_end} · ${q.scope_label}`; }
    if (claim.value_kind === "milestone") {
      if (v.date_relation === "on_or_before") return `${human(v.status)}: ${human(v.milestone_type)} · occurred on or before ${v.date_high} · lower bound and exact date unknown`;
      return `${human(v.status)}: ${human(v.milestone_type)} · ${v.date_low} to ${v.date_high} · precision ${v.date_precision || "not specified"} · midpoint ${v.date_base || "null"}`;
    }
    if (claim.value_kind === "scalar") return display(v.value) + (v.unit ? " " + v.unit : "");
    return JSON.stringify(v);
  }
  function claimCard(claim) {
    const box = node("details", undefined, "claim"), title = node("summary");
    title.append(node("span", human(claim.claim_kind), "tag"), node("span", human(claim.predicate)), node("span", valueSummary(claim), "meta"));
    const content = node("div"); content.append(node("p", valueSummary(claim), "value"));
    const {value, evidence, dependencies, ...metadata} = claim;
    content.append(fields({...metadata, value}), node("h3", "Direct evidence"));
    if (!evidence.length) content.append(node("p", "No direct links. Inspect the explicit dependencies below."));
    for (const e of evidence) { const item = node("div"); item.append(node("p", `${e.publisher} · ${e.source_family} · role ${e.role}`), safeLink(e.document_url), fields(e)); content.append(item); }
    content.append(node("h3", "Dependencies"), node("pre", JSON.stringify(dependencies, null, 2)));
    box.append(title, content); return box;
  }
  function renderDossier(report) {
    const root = $("dossier"); root.replaceChildren(node("h2", report.entity.display_name || report.entity.stable_key), node("p", `${human(report.entity.kind)} · ${report.entity.id}`, "meta"), node("p", `World date ${report.as_of} · known by ${report.recorded_at}`, "meta"), node("p", "Exact record only. Similar names, phases and related entities are not merged.", "boundary"));
    root.append(disclosure("Registry identity and creation", fields(report.entity)));
    const sections = [
      ["Source statements", "source_statements", "Publisher reports known at this cutoff, including unknown and future effective dates. Not physical-current facts.", true],
      ["World-current claims", "world_current_claims", "Selected by world and knowledge time. Each observation, reconciliation or estimate keeps its kind.", true],
      ["Recorded history", "recorded_history", "Includes superseded versions. History is not extra current corroboration.", false],
      ["Dependency context", "dependency_context", "Dependency-only claims may concern other entities or dates. They do not establish identity or phase allocation.", false]
    ];
    for (const [title, key, note, open] of sections) { const content = node("div"); content.append(node("p", note, "muted")); if (!report[key].length) content.append(node("p", "No eligible records. This does not imply zero capacity, closure or inactivity.")); for (const claim of report[key]) content.append(claimCard(claim)); root.append(disclosure(`${title} · ${report[key].length}`, content, open)); }
    root.append(disclosure("Evidence coverage — not a confidence score", fields(report.evidence_summary)));
    const clocks = node("div"); for (const run of report.ingestion_runs) clocks.append(fields(run)); if (!report.ingestion_runs.length) clocks.append(node("p", "No explicit ingestion run references.")); root.append(disclosure("Ingestion clocks", clocks));
    const boundaries = node("ul"); for (const text of report.boundaries) boundaries.append(node("li", text)); root.append(disclosure("Interpretation and limits", boundaries));
    const jsonLink = node("a", "Open complete dossier JSON"); jsonLink.href = "api/dossier?" + new URLSearchParams({entity_id: report.entity.id, as_of: report.as_of, recorded_at: report.recorded_at}); jsonLink.target = "_blank"; jsonLink.rel = "noopener noreferrer"; const download = node("p"); download.append(jsonLink); root.append(download);
  }
  async function select(entity, button) {
    const generation = state.generation, selection = ++state.selection;
    $("dossier").replaceChildren(node("h2", "Loading exact-entity evidence…"));
    document.querySelectorAll("#list button").forEach(b => b.setAttribute("aria-pressed", String(b === button)));
    try {
      const report = await get("api/dossier", entity.dossier_selection, state.request.signal);
      if (generation !== state.generation || selection !== state.selection) return;
      renderDossier(report); $("dossier").focus({preventScroll: true});
    } catch (error) { if (error.name !== "AbortError" && generation === state.generation && selection === state.selection) $("dossier").replaceChildren(node("h2", "Dossier unavailable"), node("p", error.message)); }
  }
  function renderResults(report) {
    state.page = report; $("list").replaceChildren();
    $("counts").textContent = `${report.total_matched_entities} matches / ${report.visible_registry_entities} visible registry entries`;
    $("page").textContent = report.returned_entities ? `${report.offset + 1}–${report.offset + report.returned_entities}` : "No matches";
    $("previous").disabled = report.offset === 0; $("next").disabled = report.next_offset === null;
    for (const e of report.results) { const button = node("button"); button.type = "button"; button.setAttribute("aria-pressed", "false"); button.append(node("span", e.display_name || e.stable_key), node("span", `${human(e.kind)} · ${e.known_source_statement_count} source reports · ${e.world_current_claim_count} world-current claims`, "meta"), node("span", e.direct_sources.map(s => s.publisher).filter((s, i, a) => a.indexOf(s) === i).join(" · ") || "No selected direct-source labels", "meta"), node("span", e.id, "meta")); button.addEventListener("click", () => select(e, button)); const li = node("li"); li.append(button); $("list").append(li); }
    if (!report.results.length) $("list").append(node("li", "No matching retained records. Try another literal name or publisher."));
  }
  function renderChanges(report) {
    state.page = report; $("list").replaceChildren();
    $("counts").textContent = `${report.total_events} recorded events · ${report.distinct_claims} claims · ${report.distinct_subjects} exact subjects`;
    $("page").textContent = report.returned_events ? `${report.offset + 1}–${report.offset + report.returned_events}` : "No recorded events";
    $("previous").disabled = report.offset === 0; $("next").disabled = report.next_offset === null;
    for (const event of report.events) {
      const li = node("li", undefined, "knowledge-event"), e = event.entity, button = node("button");
      button.type = "button"; button.setAttribute("aria-pressed", "false");
      button.append(node("span", e.display_name || e.stable_key), node("span", `Open exact dossier · ${e.id}`, "meta"));
      button.addEventListener("click", () => select({...e, dossier_selection: event.dossier_selection}, button));
      li.append(button, node("p", `${human(event.event_type)} · ${event.event_at}`, "meta"), claimCard(event.claim));
      if (event.event_type === "claim_superseded") li.append(node("p", "Bookkeeping only; not closure, cancellation or capacity loss.", "meta"));
      li.append(disclosure("Event identity", fields({event_id: event.event_id, event_at: event.event_at, event_type: event.event_type})));
      $("list").append(li);
    }
    if (!report.events.length) $("list").append(node("li", "No stored events in this window. This does not establish no real-world change."));
    const root = $("dossier"); root.replaceChildren(node("h2", "Recorded knowledge, not manufacturing events"),
      node("p", `${report.event_counts.claim_admitted} admissions · ${report.event_counts.claim_superseded} supersessions`),
      node("p", "Expand a claim to inspect its value and evidence. Open an exact dossier for broader context. World date affects the dossier, not this event list."));
    root.append(disclosure("Window and population", fields({since_exclusive: report.since, recorded_at_inclusive: report.recorded_at,
      population_sha256: report.population_sha256, total_events: report.total_events, returned_events: report.returned_events,
      page_validation_cutoff: report.page_validation_cutoff})));
    const dependencies = node("div"); for (const claim of report.dependency_context) dependencies.append(claimCard(claim));
    root.append(disclosure(`Page dependency context · ${report.dependency_context.length}`, dependencies));
    const runs = node("div"); for (const run of report.ingestion_runs) runs.append(fields(run)); root.append(disclosure("Page ingestion clocks", runs));
    const boundaries = node("ul"); for (const text of report.boundaries) boundaries.append(node("li", text)); root.append(disclosure("Interpretation and limits", boundaries));
  }
  function modeControls() {
    const changes = $("view-mode").value === "changes";
    $("query-label").hidden = changes; $("query").disabled = changes;
    $("since-label").hidden = !changes; $("since").disabled = !changes; $("since").required = changes;
    $("search").textContent = changes ? "Load evidence" : "Search";
    $("list-title").textContent = changes ? "New evidence" : "Registry records";
    $("query-hint").textContent = changes ? "Stored admissions and supersessions, not physical events or alerts. Start is exclusive; Known by is inclusive. World date only selects dossier context." : "Literal metadata search, not aliases or document full text. Similar names remain separate records.";
  }
  async function search(offset = 0, newQuery = true) {
    state.request?.abort(); state.request = new AbortController(); const generation = ++state.generation;
    if (newQuery) { state.mode = $("view-mode").value === "changes" ? "changes" : "registry"; state.page = null;
      state.query = {as_of: $("as-of").value, recorded_at: $("recorded-at").value.trim()};
      if (state.mode === "changes") state.query.since = $("since").value.trim();
      else if ($("query").value.trim()) state.query.query = $("query").value.trim();
      if ($("kind").value) state.query.kind = $("kind").value;
    }
    const params = {...state.query, limit: 50, offset};
    if (state.mode === "changes" && !newQuery && state.page) params.population_sha256 = state.page.population_sha256;
    $("status").textContent = state.mode === "changes" ? "Loading recorded evidence events…" : "Searching the retained snapshot…"; $("previous").disabled = true; $("next").disabled = true; $("counts").textContent = ""; $("page").textContent = ""; $("list").replaceChildren(); $("dossier").replaceChildren(node("h2", "Inspect an exact record"), node("p", "Select a result after the search completes."));
    try { const report = await get(state.mode === "changes" ? "api/changes" : "api/search", params, state.request.signal); if (generation !== state.generation) return;
      if (state.mode === "changes") { renderChanges(report); $("status").textContent = `Learned after ${report.since} through ${report.recorded_at}. These are stored knowledge events, not physical changes.`; }
      else { renderResults(report); $("status").textContent = `Results: world date ${report.as_of} · known by ${report.recorded_at}. Counts are not a facility census or physical-capacity totals.`; }
    }
    catch (error) { if (error.name !== "AbortError" && generation === state.generation) $("status").textContent = error.message; }
  }
  $("filters").addEventListener("submit", event => { event.preventDefault(); search(); });
  $("previous").addEventListener("click", () => search(Math.max(0, state.page.offset - 50), false));
  $("next").addEventListener("click", () => search(state.page.next_offset, false));
  $("filters").addEventListener("input", () => { state.request?.abort(); ++state.generation; state.page = null; modeControls(); $("list").replaceChildren(); $("counts").textContent = ""; $("page").textContent = ""; $("previous").disabled = true; $("next").disabled = true; $("dossier").replaceChildren(node("h2", "Search inputs changed"), node("p", "Submit to inspect evidence at the new cutoffs.")); $("status").textContent = "Inputs changed. Submit to apply."; });
  get("api/config").then(config => { $("as-of").value = config.as_of; $("recorded-at").value = config.recorded_at; $("since").value = config.suggested_since || ""; $("snapshot").textContent = config.snapshot; for (const kind of config.entity_kinds) { const option = node("option", human(kind)); option.value = kind; $("kind").append(option); } $("search").disabled = false; modeControls(); search(); }).catch(error => { $("status").textContent = error.message; });
})();
