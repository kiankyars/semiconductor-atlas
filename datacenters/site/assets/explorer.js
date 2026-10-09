import { AtlasMap, loadBasemap, project, radiusFor, escapeHtml, STATUS_GROUP } from "./map.js";
import { STATUS_LABELS, SCOPE_SHORT, fmtMW, fmtMoney, fmtDate, fmtNumber, dateKey } from "./format.js";

const root = document.querySelector("[data-explorer]");
const base = root.dataset.base;
const $ = (sel) => root.querySelector(sel);
const STATUS_ORDER = Object.keys(STATUS_LABELS);
const LIST_FIELDS = ["aliases", "workloads", "developers", "owners", "operators", "tenants", "power_source_types"];

const state = { q: "", status: "", region: "", country: "", workload: "", minmw: "", osm: false, wd: false, sort: "planned_power_mw", dir: "desc" };
let projects = [], columns = [], map = null, layers = { osm: null, wd: null }, lastRows = [];

function readUrl() {
  const p = new URLSearchParams(location.search);
  for (const key of Object.keys(state)) {
    if (!p.has(key)) continue;
    state[key] = typeof state[key] === "boolean" ? p.get(key) === "1" : p.get(key);
  }
}

function writeUrl() {
  const p = new URLSearchParams();
  for (const [key, value] of Object.entries(state)) {
    if (key === "sort" && value === "planned_power_mw") continue;
    if (key === "dir" && value === "desc") continue;
    if (value === true) p.set(key, "1");
    else if (value && value !== true) p.set(key, value);
  }
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

function haystack(r) {
  return [r.name, r.locality, r.admin2, r.admin1, r.country_name, r.country, ...(r.aliases || []), ...(r.developers || []),
    ...(r.owners || []), ...(r.operators || []), ...(r.tenants || [])].join(" ").toLowerCase();
}

function matches(r) {
  if (state.q) {
    const words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    const text = r._text || (r._text = haystack(r));
    if (!words.every((w) => text.includes(w))) return false;
  }
  if (state.status) {
    if (state.status.startsWith("group:")) { if (STATUS_GROUP[r.status] !== state.status.slice(6)) return false; }
    else if (r.status !== state.status) return false;
  }
  if (state.region && r.region !== state.region) return false;
  if (state.country && r.country !== state.country) return false;
  if (state.workload === "ai" && !(r.workloads || []).some((w) => w.startsWith("ai_"))) return false;
  if (state.workload === "other" && (r.workloads || []).some((w) => w.startsWith("ai_"))) return false;
  if (state.minmw && !(r.planned_power_mw >= Number(state.minmw))) return false;
  return true;
}

function compare(a, b) {
  const key = state.sort, dir = state.dir === "asc" ? 1 : -1;
  let x = a[key], y = b[key];
  if (key === "location") { x = `${a.country_name} ${a.admin1 || ""} ${a.locality || ""}`; y = `${b.country_name} ${b.admin1 || ""} ${b.locality || ""}`; }
  if (key === "status") { x = STATUS_ORDER.indexOf(a.status); y = STATUS_ORDER.indexOf(b.status); }
  if (key === "first_operational") { x = dateKey(a.first_operational || a.first_operational_target); y = dateKey(b.first_operational || b.first_operational_target); }
  if (key === "investment_value") { x = a.investment_currency === "USD" ? a.investment_value : null; y = b.investment_currency === "USD" ? b.investment_value : null; }
  const xn = x == null || x === "", yn = y == null || y === "";
  if (xn || yn) return xn && yn ? a.name.localeCompare(b.name) : xn ? 1 : -1;
  if (typeof x === "string") return x.localeCompare(y) * dir || a.name.localeCompare(b.name);
  return (x - y) * dir || a.name.localeCompare(b.name);
}

function statusCell(status) {
  const group = STATUS_GROUP[status] || "planned";
  return `<span class="status"><span class="dot ${group}" aria-hidden="true"></span>${STATUS_LABELS[status] || status}</span>`;
}

function powerText(r) {
  const gen = r.onsite_generation_mw != null ? `<span class="sub">${fmtMW(r.onsite_generation_mw, r.onsite_generation_mw_high, r.onsite_generation_qualifier)} on-site gen.</span>` : "";
  if (r.planned_power_mw == null) return gen;
  const scope = SCOPE_SHORT[r.planned_power_scope];
  return `${fmtMW(r.planned_power_mw, r.planned_power_mw_high, r.planned_power_qualifier)}${scope ? `<span class="scope">${scope}</span>` : ""}${gen}`;
}

function partiesText(r) {
  const lead = (r.developers || []).concat(r.owners || []).filter((v, i, a) => a.indexOf(v) === i).slice(0, 2).join(", ");
  const tenants = (r.tenants || []).slice(0, 2).join(", ");
  return `${escapeHtml(lead)}${tenants ? `<span class="sub">for ${escapeHtml(tenants)}</span>` : ""}`;
}

function renderTable(rows) {
  const body = $("#rows");
  if (!rows.length) { body.innerHTML = `<tr><td colspan="8" class="empty">No projects match these filters.</td></tr>`; return; }
  body.innerHTML = rows.map((r) => {
    const place = [r.locality, r.admin1].filter(Boolean).join(", ");
    const first = r.first_operational ? fmtDate(r.first_operational) : r.first_operational_target ? `${fmtDate(r.first_operational_target)} <span class="sub">target</span>` : "";
    return `<tr data-id="${escapeHtml(r.id)}">
      <td><a href="${base}projects/${encodeURIComponent(r.id)}/">${escapeHtml(r.name)}</a>${(r.aliases || []).length ? `<span class="sub">${escapeHtml(r.aliases.slice(0, 2).join(" · "))}</span>` : ""}</td>
      <td>${escapeHtml(place)}<span class="sub">${escapeHtml(r.country_name || r.country)}</span></td>
      <td>${statusCell(r.status)}<span class="sub">as of ${fmtDate(r.status_as_of)}</span></td>
      <td class="num">${powerText(r)}</td>
      <td class="num">${r.investment_value != null ? escapeHtml(fmtMoney(r.investment_value, r.investment_value_high, r.investment_currency, r.investment_qualifier)) : ""}</td>
      <td>${partiesText(r)}</td>
      <td>${first}</td>
      <td class="num"><a href="${base}projects/${encodeURIComponent(r.id)}/#sources">${r.source_count}</a></td>
    </tr>`;
  }).join("");
}

function renderHeaders() {
  root.querySelectorAll("th[data-sort]").forEach((th) => {
    const key = th.dataset.sort;
    th.setAttribute("aria-sort", key === state.sort ? (state.dir === "asc" ? "ascending" : "descending") : "none");
  });
}

// ---- charts (single-hue bars; value labels; hover tooltips) ----
function barChart(el, items, { valueFormat = fmtNumber, href } = {}) {
  if (!items.length) { el.innerHTML = `<p class="empty">Nothing to chart for these filters.</p>`; return; }
  const max = Math.max(...items.map((d) => d.value));
  const width = Math.max(300, Math.round(el.clientWidth || 560));
  const rowH = 26, labelW = Math.min(168, Math.round(width * 0.36)), valueW = 78, plotW = width - labelW - valueW;
  const maxChars = Math.max(10, Math.floor(labelW / 6.6));
  const height = items.length * rowH + 4;
  const bars = items.map((d, i) => {
    const w = Math.max(2, (d.value / max) * plotW);
    const y = i * rowH + 4;
    const label = d.label.length > maxChars ? `${d.label.slice(0, maxChars - 1)}…` : d.label;
    const rect = `<path class="bar" data-i="${i}" d="M${labelW},${y + 4}h${w - 4}a4,4 0 0 1 4,4v${rowH - 16}a4,4 0 0 1 -4,4h${-(w - 4)}z"></path>`;
    const linked = href && d.href;
    return `<g><rect x="0" y="${y}" width="${width}" height="${rowH}" fill="transparent" data-i="${i}"></rect>${linked ? `<a href="${d.href}" data-i="${i}">` : ""}<text x="${labelW - 8}" y="${y + rowH / 2 + 1}" text-anchor="end" dominant-baseline="middle" data-i="${i}">${escapeHtml(label)}</text>${rect}${linked ? "</a>" : ""}
      <text class="value-label" x="${labelW + w + 6}" y="${y + rowH / 2 + 1}" dominant-baseline="middle" data-i="${i}">${escapeHtml(valueFormat(d.value, d))}</text></g>`;
  }).join("");
  const role = href ? `role="group"` : `role="img"`;
  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" ${role} aria-label="${escapeHtml(el.dataset.label || "Bar chart")}">
    <line class="baseline" x1="${labelW}" x2="${labelW}" y1="0" y2="${height}"></line>${bars}</svg><div class="tooltip" hidden></div>`;
  const tip = el.querySelector(".tooltip");
  el.querySelector("svg").addEventListener("pointermove", (e) => {
    const hit = e.target.closest ? e.target.closest("[data-i]") : null;
    const i = hit ? hit.dataset.i : undefined;
    if (i === undefined) { tip.hidden = true; return; }
    const d = items[Number(i)];
    tip.innerHTML = `<strong>${escapeHtml(d.label)}</strong>${escapeHtml(d.tip || valueFormat(d.value, d))}`;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    tip.style.left = `${Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${e.clientY - r.top + 12}px`;
  });
  el.querySelector("svg").addEventListener("pointerleave", () => { tip.hidden = true; });
}

function columnChart(el, items) {
  if (!items.length) { el.innerHTML = `<p class="empty">Nothing to chart for these filters.</p>`; return; }
  const width = Math.max(300, Math.round(el.clientWidth || 560)), height = 220, left = 30, bottom = 24, top = 10;
  const max = Math.max(...items.map((d) => d.value));
  const step = (width - left) / items.length;
  const bw = Math.min(42, step - 6);
  const ticks = [0, Math.ceil(max / 2), max];
  const grid = ticks.map((t) => { const y = top + (1 - t / max) * (height - top - bottom); return `<line class="gridline" x1="${left}" x2="${width}" y1="${y}" y2="${y}"></line><text x="${left - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${t}</text>`; }).join("");
  const cols = items.map((d, i) => {
    const h = (d.value / max) * (height - top - bottom);
    const x = left + i * step + (step - bw) / 2, y = height - bottom - h;
    const r = Math.min(4, h / 2, bw / 2);
    return `<path class="bar" data-i="${i}" d="M${x},${height - bottom}v${-(h - r)}a${r},${r} 0 0 1 ${r},${-r}h${bw - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${h - r}z"></path>
      <text x="${x + bw / 2}" y="${height - bottom + 15}" text-anchor="middle">${escapeHtml(d.label)}</text>
      <rect x="${left + i * step}" y="${top}" width="${step}" height="${height - top - bottom}" fill="transparent" data-i="${i}"></rect>`;
  }).join("");
  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${escapeHtml(el.dataset.label || "Column chart")}">${grid}<line class="baseline" x1="${left}" x2="${width}" y1="${height - bottom}" y2="${height - bottom}"></line>${cols}</svg><div class="tooltip" hidden></div>`;
  const tip = el.querySelector(".tooltip");
  el.querySelector("svg").addEventListener("pointermove", (e) => {
    const i = e.target.dataset ? e.target.dataset.i : undefined;
    if (i === undefined) { tip.hidden = true; return; }
    const d = items[Number(i)];
    tip.innerHTML = `<strong>${escapeHtml(d.label)}</strong>${d.value} project${d.value === 1 ? "" : "s"} announced`;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    tip.style.left = `${Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${e.clientY - r.top - 40}px`;
  });
  el.querySelector("svg").addEventListener("pointerleave", () => { tip.hidden = true; });
}

function renderCharts(rows) {
  lastRows = rows;
  const counts = STATUS_ORDER.map((s) => ({ label: STATUS_LABELS[s], value: rows.filter((r) => r.status === s).length })).filter((d) => d.value);
  barChart(document.getElementById("chart-status"), counts, { valueFormat: (v) => `${v}` });
  const top = rows.filter((r) => r.planned_power_mw != null).sort((a, b) => b.planned_power_mw - a.planned_power_mw).slice(0, 12)
    .map((r) => ({ label: r.name, value: r.planned_power_mw, href: `${base}projects/${encodeURIComponent(r.id)}/`,
      tip: `${fmtMW(r.planned_power_mw, r.planned_power_mw_high, r.planned_power_qualifier)} planned · scope: ${r.planned_power_scope.replace("_", " ")} · as of ${fmtDate(r.planned_power_as_of)}` }));
  barChart(document.getElementById("chart-power"), top, { valueFormat: (v, d) => fmtMW(v), href: true });
  const byCountry = new Map();
  rows.forEach((r) => byCountry.set(r.country_name || r.country, (byCountry.get(r.country_name || r.country) || 0) + 1));
  const countries = [...byCountry].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value || a.label.localeCompare(b.label)).slice(0, 12);
  barChart(document.getElementById("chart-country"), countries, { valueFormat: (v) => `${v}` });
  const years = new Map();
  rows.forEach((r) => { if (r.announced) years.set(r.announced.slice(0, 4), (years.get(r.announced.slice(0, 4)) || 0) + 1); });
  const ys = [...years.keys()].sort();
  const series = [];
  if (ys.length) for (let y = Number(ys[0]); y <= Number(ys[ys.length - 1]); y++) series.push({ label: String(y), value: years.get(String(y)) || 0 });
  columnChart(document.getElementById("chart-years"), series);
}

function csvEscape(v) {
  if (v == null) return "";
  let s = Array.isArray(v) ? v.join("; ") : String(v);
  if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`; // keep spreadsheets from evaluating text
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadCsv(rows) {
  const lines = [columns.join(",")].concat(rows.map((r) => columns.map((c) => csvEscape(r[c])).join(",")));
  const blob = new Blob(["\ufeff" + lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "data-center-builds-filtered.csv";
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function tooltipFor(p) {
  if (p.layer === "osm" || p.layer === "wikidata") {
    return `<strong>${escapeHtml(p.name || "Unnamed data center")}</strong>${escapeHtml(p.operator || "")}<br>${p.layer === "osm" ? "OpenStreetMap feature (uncurated)" : "Wikidata item (uncurated)"}`;
  }
  const r = p.row;
  const place = [r.locality, r.admin1, r.country_name].filter(Boolean).join(", ");
  return `<strong>${escapeHtml(r.name)}</strong>${escapeHtml(STATUS_LABELS[r.status])} · ${escapeHtml(place)}${r.planned_power_mw != null ? `<br>${fmtMW(r.planned_power_mw, r.planned_power_mw_high, r.planned_power_qualifier)} planned${SCOPE_SHORT[r.planned_power_scope] ? ` (${SCOPE_SHORT[r.planned_power_scope]})` : ""}` : ""}`;
}

function clearSelection() {
  const card = $("#selection");
  if (card) card.hidden = true;
  if (map) { map.highlight = null; map.draw(); }
}

function selectPoint(p) {
  const card = $("#selection");
  if (p.layer === "osm" || p.layer === "wikidata") {
    card.innerHTML = `<button class="linkish close" type="button" aria-label="Close">✕</button><strong>${escapeHtml(p.name || "Unnamed data center")}</strong><span class="sub">${p.layer === "osm" ? "OpenStreetMap feature · ODbL" : "Wikidata item · CC0"}</span>${p.operator ? `<p>${escapeHtml(p.operator)}</p>` : ""}<a href="${escapeHtml(p.url)}">Open source record ↗</a>`;
  } else {
    const r = p.row;
    root.querySelectorAll("#rows tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.id === r.id));
    card.innerHTML = `<button class="linkish close" type="button" aria-label="Close">✕</button><strong>${escapeHtml(r.name)}</strong><span class="sub">${statusCell(r.status)}</span>
      <p>${escapeHtml([r.locality, r.admin1, r.country_name].filter(Boolean).join(", "))}</p>
      ${r.planned_power_mw != null ? `<p>${fmtMW(r.planned_power_mw, r.planned_power_mw_high, r.planned_power_qualifier)} planned${SCOPE_SHORT[r.planned_power_scope] ? ` (${SCOPE_SHORT[r.planned_power_scope]})` : ""}</p>` : ""}
      <a href="${base}projects/${encodeURIComponent(r.id)}/">Open project page →</a>`;
  }
  card.hidden = false;
  card.querySelector(".close").addEventListener("click", clearSelection);
  map.highlight = p; map.draw();
}

async function loadLayer(name) {
  if (layers[name]) return layers[name];
  const url = `${base}assets/layers/${name === "osm" ? "osm" : "wikidata"}.json`;
  const data = await getJson(url);
  layers[name] = data.points.map(([lon, lat, label, operator, ref]) => {
    const [x, y] = project(lon, lat);
    return { x, y, name: label, operator, layer: name === "osm" ? "osm" : "wikidata",
      url: name === "osm" ? `https://www.openstreetmap.org/${ref}` : `https://www.wikidata.org/wiki/${ref}` };
  });
  return layers[name];
}

async function updateLayers() {
  if (!map) return;
  try {
    if (state.osm) await loadLayer("osm");
    if (state.wd) await loadLayer("wd");
  } catch (err) {
    console.error(err);
  }
  // Read the toggles again after loading, so a quick on/off cannot leave a layer drawn.
  const ctx = [];
  if (state.osm && layers.osm) ctx.push(...layers.osm);
  if (state.wd && layers.wd) ctx.push(...layers.wd);
  map.setContext(ctx);
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

function update({ refit = false } = {}) {
  const rows = projects.filter(matches).sort(compare);
  if (map && map.highlight && map.highlight.row && !rows.includes(map.highlight.row)) clearSelection();
  $("#count").textContent = `${rows.length} of ${projects.length} projects`;
  renderTable(rows);
  renderHeaders();
  renderCharts(rows);
  if (map) {
    map.setPoints(rows.map((r) => ({ ...r._xy, r: radiusFor(r.planned_power_mw), status: r.status, row: r })));
    if (refit) map.fitPoints(rows.map((r) => r._xy), 30);
  }
  writeUrl();
  return rows;
}

function fillSelect(sel, values, current) {
  const keep = sel.querySelector("option[value='']").outerHTML;
  sel.innerHTML = keep + values.map(([v, label]) => `<option value="${escapeHtml(v)}">${escapeHtml(label)}</option>`).join("");
  sel.value = values.some(([v]) => v === current) ? current : "";
}

function syncCountryOptions() {
  const list = new Map();
  projects.filter((r) => !state.region || r.region === state.region).forEach((r) => list.set(r.country, r.country_name || r.country));
  fillSelect($("#f-country"), [...list].sort((a, b) => a[1].localeCompare(b[1])), state.country);
  state.country = $("#f-country").value;
}

async function init() {
  readUrl();
  const api = await getJson(`${base}api/v1/projects.json`);
  columns = api.columns;
  projects = api.projects.map((r) => ({ ...r, _xy: (([x, y]) => ({ x, y }))(project(r.lon, r.lat)) }));
  const regions = [...new Set(projects.map((r) => r.region).filter(Boolean))].sort();
  fillSelect($("#f-region"), regions.map((r) => [r, r]), state.region);
  state.region = $("#f-region").value;
  syncCountryOptions();
  // Unknown values from a hand-edited URL fall back to "all" instead of silently hiding rows.
  for (const [id, key] of [["#f-q", "q"], ["#f-status", "status"], ["#f-workload", "workload"], ["#f-minmw", "minmw"]]) {
    $(id).value = state[key];
    state[key] = $(id).value;
  }
  if (!columns.includes(state.sort) && !["location", "status"].includes(state.sort)) state.sort = "planned_power_mw";
  if (!["asc", "desc"].includes(state.dir)) state.dir = "desc";
  $("#f-osm").checked = state.osm; $("#f-wd").checked = state.wd;

  let timer;
  $("#f-q").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value.trim(); update(); }, 120); });
  $("#f-status").addEventListener("change", (e) => { state.status = e.target.value; update(); });
  $("#f-region").addEventListener("change", (e) => { state.region = e.target.value; syncCountryOptions(); update({ refit: true }); });
  $("#f-country").addEventListener("change", (e) => { state.country = e.target.value; update({ refit: true }); });
  $("#f-workload").addEventListener("change", (e) => { state.workload = e.target.value; update(); });
  $("#f-minmw").addEventListener("change", (e) => { state.minmw = e.target.value; update(); });
  $("#f-osm").addEventListener("change", (e) => { state.osm = e.target.checked; writeUrl(); updateLayers(); });
  $("#f-wd").addEventListener("change", (e) => { state.wd = e.target.checked; writeUrl(); updateLayers(); });
  $("#reset").addEventListener("click", () => {
    Object.assign(state, { q: "", status: "", region: "", country: "", workload: "", minmw: "" });
    for (const id of ["#f-q", "#f-status", "#f-region", "#f-workload", "#f-minmw"]) $(id).value = "";
    syncCountryOptions();
    clearSelection();
    update();
    if (map) map.fitWorld();
  });
  $("#download").addEventListener("click", () => downloadCsv(projects.filter(matches).sort(compare)));
  root.querySelectorAll("th[data-sort] button").forEach((b) => b.addEventListener("click", () => {
    const key = b.parentElement.dataset.sort;
    if (state.sort === key) state.dir = state.dir === "asc" ? "desc" : "asc";
    else { state.sort = key; state.dir = ["name", "location", "status"].includes(key) ? "asc" : "desc"; }
    update();
  }));
  let resizeTimer;
  new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => renderCharts(lastRows), 150); })
    .observe(document.getElementById("chart-power"));
  const filtered = update();

  try {
    const basemap = await loadBasemap(`${base}assets/basemap.json`);
    map = new AtlasMap($("#map"), basemap, {
      tooltip: tooltipFor, onSelect: selectPoint,
      onReset: () => map.fitPoints(projects.filter(matches).map((r) => r._xy), 30),
      label: "World map of the filtered data center builds. The table below lists the same projects.",
    });
    update();
    if (state.country || state.region || state.q) map.fitPoints(filtered.map((r) => r._xy), 30);
    if (state.osm || state.wd) updateLayers();
  } catch (err) {
    console.error(err);
    const legend = $("#map .legend");
    if (legend) legend.insertAdjacentHTML("beforeend", `<p class="note">The interactive map could not load. The table, filters and downloads still work.</p>`);
  }
}

init().catch((err) => {
  console.error(err);
  $("#count").textContent = "Could not load the project list. Downloads on the Data & API page still work.";
});
