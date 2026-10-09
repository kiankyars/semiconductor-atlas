// Dependency-free world map: Natural Earth basemap on a canvas, Equal Earth projection.
const A1 = 1.340264, A2 = -0.081106, A3 = 0.000893, A4 = 0.003796;
const M = Math.sqrt(3) / 2;
const X_MAX = Math.PI / (M * A1);
const Y_MAX = 1.3173627591574;
export const PLANE_WIDTH = 2000;
const SCALE = PLANE_WIDTH / (2 * X_MAX);

export function project(lon, lat) {
  const lam = lon * Math.PI / 180;
  const theta = Math.asin(M * Math.sin(lat * Math.PI / 180));
  const t2 = theta * theta, t6 = t2 * t2 * t2;
  const x = lam * Math.cos(theta) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)));
  const y = theta * (A1 + A2 * t2 + t6 * (A3 + A4 * t2));
  return [(x + X_MAX) * SCALE, (Y_MAX - y) * SCALE];
}

export const STATUS_GROUP = {
  operational: "operating", partially_operational: "operating",
  under_construction: "construction",
  proposed: "planned", announced: "planned",
  paused: "inactive", cancelled: "inactive",
};

let basemapPromise = null;
export function loadBasemap(url) {
  if (!basemapPromise) basemapPromise = fetch(url).then((r) => r.json()).then((b) => ({
    ...b,
    landPath: new Path2D(b.land),
    adminPath: new Path2D(b.admin1),
    gratPath: new Path2D(b.graticule),
    outlinePath: new Path2D(b.outline),
  }));
  return basemapPromise;
}

function cssVar(el, name) { return getComputedStyle(el).getPropertyValue(name).trim(); }

export function radiusFor(mw) {
  if (!mw) return 4;
  return Math.max(4, Math.min(13, 3 + 2 * Math.sqrt(mw / 100)));
}

export class AtlasMap {
  constructor(container, basemap, options = {}) {
    this.container = container;
    this.basemap = basemap;
    this.options = options;
    this.canvas = document.createElement("canvas");
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label", options.label || "World map of data center builds");
    container.prepend(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.points = [];
    this.context = [];
    this.highlight = null;
    this.k = 1; this.tx = 0; this.ty = 0;
    this.tooltip = container.querySelector(".tooltip");
    this._bind();
    this.resize();
    new ResizeObserver(() => { this.resize(); }).observe(container);
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.draw());
  }

  setPoints(points) { this.points = points.slice().sort((a, b) => b.r - a.r); this.draw(); }
  setContext(points) { this.context = points; this.draw(); }

  resize() {
    const rect = this.container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const first = !this.w;
    this.w = rect.width; this.h = rect.height; this.dpr = dpr;
    this.canvas.width = Math.round(rect.width * dpr);
    this.canvas.height = Math.round(rect.height * dpr);
    if (first) this.fitWorld(); else this.draw();
  }

  fitWorld() {
    const k = Math.min(this.w / this.basemap.width, this.h / this.basemap.height) * 0.98;
    this.k = k;
    this.tx = (this.w - this.basemap.width * k) / 2;
    this.ty = (this.h - this.basemap.height * k) / 2;
    this.minK = k * 0.9;
    this.draw();
  }

  fitPoints(points, maxK) {
    if (!points.length) { this.fitWorld(); return; }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of points) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
    const pad = 40;
    const kx = (this.w - 2 * pad) / Math.max(x1 - x0, 1);
    const ky = (this.h - 2 * pad) / Math.max(y1 - y0, 1);
    const worldK = Math.min(this.w / this.basemap.width, this.h / this.basemap.height) * 0.98;
    this.minK = worldK * 0.9;
    this.k = Math.max(worldK, Math.min(kx, ky, maxK || 60));
    this.tx = this.w / 2 - ((x0 + x1) / 2) * this.k;
    this.ty = this.h / 2 - ((y0 + y1) / 2) * this.k;
    this.draw();
  }

  zoomAt(factor, sx, sy) {
    const k = Math.max(this.minK || 0.1, Math.min(400, this.k * factor));
    const f = k / this.k;
    this.tx = sx - (sx - this.tx) * f;
    this.ty = sy - (sy - this.ty) * f;
    this.k = k;
    this.draw();
  }

  draw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = null; this._draw(); });
  }

  _draw() {
    const { ctx, dpr, k, tx, ty, basemap } = this;
    const el = this.container;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.setTransform(k * dpr, 0, 0, k * dpr, tx * dpr, ty * dpr);
    ctx.fillStyle = cssVar(el, "--water");
    ctx.fill(basemap.outlinePath);
    ctx.lineWidth = 0.6 / k;
    ctx.strokeStyle = cssVar(el, "--grid");
    ctx.stroke(basemap.gratPath);
    ctx.fillStyle = cssVar(el, "--land");
    ctx.fill(basemap.landPath);
    if (k > 1.6) {
      ctx.lineWidth = 0.7 / k;
      ctx.strokeStyle = cssVar(el, "--admin-line");
      ctx.stroke(basemap.adminPath);
    }
    ctx.lineWidth = 0.8 / k;
    ctx.strokeStyle = cssVar(el, "--land-line");
    ctx.stroke(basemap.landPath);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (this.context.length) {
      const colour = cssVar(el, "--context");
      const s = Math.max(1.6, Math.min(3.2, 1.2 + k / 6));
      for (const p of this.context) {
        const x = p.x * k + tx, y = p.y * k + ty;
        if (x < -5 || y < -5 || x > this.w + 5 || y > this.h + 5) continue;
        ctx.fillStyle = colour; ctx.strokeStyle = colour;
        if (p.layer === "wikidata") {
          ctx.lineWidth = 1.3;
          ctx.beginPath(); ctx.moveTo(x, y - s - 1.5); ctx.lineTo(x + s + 1.5, y); ctx.lineTo(x, y + s + 1.5); ctx.lineTo(x - s - 1.5, y); ctx.closePath(); ctx.stroke();
        } else {
          ctx.beginPath(); ctx.arc(x, y, s, 0, Math.PI * 2); ctx.fill();
        }
      }
    }
    const surface = cssVar(el, "--surface");
    const colours = {
      operating: cssVar(el, "--s-operating"), construction: cssVar(el, "--s-construction"),
      planned: cssVar(el, "--s-planned"), inactive: cssVar(el, "--s-inactive"),
    };
    for (const p of this.points) {
      const x = p.x * k + tx, y = p.y * k + ty;
      if (x < -30 || y < -30 || x > this.w + 30 || y > this.h + 30) continue;
      const group = STATUS_GROUP[p.status] || "planned";
      ctx.beginPath(); ctx.arc(x, y, p.r, 0, Math.PI * 2);
      if (group === "inactive") {
        ctx.fillStyle = surface; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = colours.inactive; ctx.stroke();
      } else {
        ctx.globalAlpha = 0.88; ctx.fillStyle = colours[group]; ctx.fill(); ctx.globalAlpha = 1;
        ctx.lineWidth = 2; ctx.strokeStyle = surface; ctx.stroke();
      }
      if (p.status === "partially_operational") {
        ctx.beginPath(); ctx.arc(x, y, Math.max(1.5, p.r * 0.38), 0, Math.PI * 2);
        ctx.fillStyle = surface; ctx.fill();
      }
    }
    if (this.highlight) {
      const p = this.highlight;
      const x = p.x * k + tx, y = p.y * k + ty;
      ctx.beginPath(); ctx.arc(x, y, (p.r || 5) + 4, 0, Math.PI * 2);
      ctx.lineWidth = 2.5; ctx.strokeStyle = cssVar(el, "--ink"); ctx.stroke();
    }
  }

  hit(sx, sy) {
    const { k, tx, ty } = this;
    let best = null, bestD = Infinity;
    for (const p of this.points) {
      const dx = p.x * k + tx - sx, dy = p.y * k + ty - sy;
      const d = Math.hypot(dx, dy);
      if (d <= p.r + 5 && d < bestD) { best = p; bestD = d; }
    }
    if (best) return best;
    for (const p of this.context) {
      const dx = p.x * k + tx - sx, dy = p.y * k + ty - sy;
      const d = Math.hypot(dx, dy);
      if (d <= 6 && d < bestD) { best = p; bestD = d; }
    }
    return best;
  }

  _bind() {
    const c = this.canvas;
    const pointers = new Map();
    let drag = null, pinch = null, moved = false;
    const local = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    // Cooperative zoom: plain scrolling keeps scrolling the page until the map is clicked.
    let active = false, hintTimer = null;
    const hint = document.createElement("div");
    hint.className = "map-hint";
    hint.hidden = true;
    hint.textContent = `${/Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl"} + scroll to zoom, or click the map first`;
    this.container.append(hint);
    c.addEventListener("wheel", (e) => {
      if (!(active || e.ctrlKey || e.metaKey)) {
        hint.hidden = false;
        clearTimeout(hintTimer);
        hintTimer = setTimeout(() => { hint.hidden = true; }, 1400);
        return;
      }
      e.preventDefault();
      hint.hidden = true;
      const [sx, sy] = local(e);
      this.zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0022)), sx, sy);
    }, { passive: false });
    this.container.addEventListener("pointerleave", () => { active = false; });
    c.addEventListener("pointerdown", (e) => {
      active = true;
      c.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, local(e));
      moved = false;
      if (pointers.size === 1) { drag = { p: local(e), tx: this.tx, ty: this.ty }; c.classList.add("dragging"); }
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), k: this.k };
        drag = null;
      }
    });
    c.addEventListener("pointermove", (e) => {
      const p = local(e);
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p);
      if (pinch && pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        this.zoomAt((pinch.k * d / pinch.d) / this.k, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
        moved = true;
        return;
      }
      if (drag) {
        const dx = p[0] - drag.p[0], dy = p[1] - drag.p[1];
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        this.tx = drag.tx + dx; this.ty = drag.ty + dy; this.draw();
        this._hideTip();
        return;
      }
      if (e.pointerType === "mouse") this._hover(p);
    });
    const end = (e) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (pointers.size === 0) {
        c.classList.remove("dragging");
        if (drag && !moved) {
          const target = this.hit(...local(e));
          if (target && this.options.onSelect) this.options.onSelect(target);
          else if (target) this._hover(local(e), true);
        }
        drag = null;
      }
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("pointerleave", () => this._hideTip());
    c.addEventListener("dblclick", (e) => { const [sx, sy] = local(e); this.zoomAt(2, sx, sy); });
    const controls = this.container.querySelector(".map-controls");
    if (controls) {
      controls.querySelector("[data-zoom='in']").addEventListener("click", () => this.zoomAt(1.8, this.w / 2, this.h / 2));
      controls.querySelector("[data-zoom='out']").addEventListener("click", () => this.zoomAt(1 / 1.8, this.w / 2, this.h / 2));
      controls.querySelector("[data-zoom='reset']").addEventListener("click", () => {
        if (this.options.onReset) this.options.onReset(); else this.fitWorld();
      });
    }
  }

  _hover([sx, sy], sticky = false) {
    const target = this.hit(sx, sy);
    this.canvas.style.cursor = target ? "pointer" : "";
    if (!target || !this.tooltip || !this.options.tooltip) { if (!sticky) this._hideTip(); return; }
    this.tooltip.innerHTML = this.options.tooltip(target);
    this.tooltip.hidden = false;
    const tw = this.tooltip.offsetWidth, th = this.tooltip.offsetHeight;
    let x = sx + 14, y = sy + 14;
    if (x + tw > this.w - 8) x = sx - tw - 14;
    if (y + th > this.h - 8) y = sy - th - 14;
    this.tooltip.style.left = `${Math.max(4, x)}px`;
    this.tooltip.style.top = `${Math.max(4, y)}px`;
  }

  _hideTip() { if (this.tooltip) this.tooltip.hidden = true; }
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
