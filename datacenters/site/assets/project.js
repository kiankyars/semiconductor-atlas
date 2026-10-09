import { AtlasMap, loadBasemap, project, radiusFor, escapeHtml } from "./map.js";
import { STATUS_LABELS, fmtMW } from "./format.js";

const el = document.querySelector("[data-locator]");
if (el) {
  const base = el.dataset.base, id = el.dataset.id;
  Promise.all([
    loadBasemap(`${base}assets/basemap.json`),
    fetch(`${base}api/v1/projects.json`).then((r) => {
      if (!r.ok) throw new Error(`projects.json: HTTP ${r.status}`);
      return r.json();
    }),
  ]).then(([basemap, api]) => {
    const points = api.projects.map((r) => {
      const [x, y] = project(r.lon, r.lat);
      return { x, y, r: radiusFor(r.planned_power_mw), status: r.status, row: r };
    });
    const self = points.find((p) => p.row.id === id);
    const map = new AtlasMap(el, basemap, {
      label: el.dataset.label,
      tooltip: (p) => `<strong>${escapeHtml(p.row.name)}</strong>${escapeHtml(STATUS_LABELS[p.row.status])}${p.row.planned_power_mw != null ? ` · ${fmtMW(p.row.planned_power_mw, p.row.planned_power_mw_high, p.row.planned_power_qualifier)}` : ""}`,
      onSelect: (p) => { if (p.row.id !== id) location.href = `${base}projects/${encodeURIComponent(p.row.id)}/`; },
      onReset: () => (self ? map.fitPoints([self], 5) : map.fitWorld()),
    });
    map.setPoints(points);
    if (self) {
      map.highlight = self;
      map.fitPoints([self], 5);
    }
  }).catch((err) => console.error(err));
}
