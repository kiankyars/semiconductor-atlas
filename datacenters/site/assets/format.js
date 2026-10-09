// Display formatting shared by the explorer; mirrors dcatlas/site.py so pages agree.
export const STATUS_LABELS = {
  proposed: "Proposed", announced: "Announced", under_construction: "Under construction",
  partially_operational: "Partially operational", operational: "Operational",
  paused: "Paused", cancelled: "Cancelled",
};
export const SCOPE_SHORT = {
  it_load: "IT", facility: "facility", grid_connection: "grid", unspecified: "", onsite_generation: "on-site gen.",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function fmtNumber(value) {
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 1 });
}

function qualify(text, qualifier, highText) {
  switch (qualifier) {
    case "approximately": return `~${text}`;
    case "up_to": return `up to ${text}`;
    case "at_least": return `≥ ${text}`;
    case "more_than": return `> ${text}`;
    case "range": return `${text}–${highText}`;
    default: return text;
  }
}

export function fmtMW(value, high, qualifier) {
  if (value == null) return "";
  const gw = (v) => (v >= 1000 ? `${fmtNumber(v / 1000)} GW` : `${fmtNumber(v)} MW`);
  if (qualifier === "range" && high != null) {
    return value >= 1000 ? `${fmtNumber(value / 1000)}–${fmtNumber(high / 1000)} GW` : `${fmtNumber(value)}–${fmtNumber(high)} MW`;
  }
  return qualify(gw(value), qualifier);
}

export function compactMoney(value) {
  if (value >= 1e12) return `${fmtNumber(value / 1e12)}T`;
  if (value >= 1e9) return `${fmtNumber(value / 1e9)}B`;
  if (value >= 1e6) return `${fmtNumber(value / 1e6)}M`;
  return fmtNumber(value);
}

export function fmtMoney(value, high, currency, qualifier) {
  if (value == null) return "";
  if (qualifier === "range" && high != null) return `${currency} ${compactMoney(value)}–${compactMoney(high)}`;
  return qualify(`${currency} ${compactMoney(value)}`, qualifier);
}

export function fmtDate(value) {
  if (!value) return "";
  const [y, rest] = [value.slice(0, 4), value.slice(5)];
  if (!rest) return y;
  if (rest[0] === "Q" || rest[0] === "H") return `${rest} ${y}`;
  const parts = rest.split("-");
  const month = MONTHS[Number(parts[0]) - 1];
  return parts.length === 1 ? `${month} ${y}` : `${Number(parts[1])} ${month} ${y}`;
}

// Sortable start-of-interval key for partial dates (YYYY, YYYY-MM, YYYY-MM-DD, YYYY-Qn, YYYY-Hn).
export function dateKey(value) {
  if (!value) return null;
  const year = Number(value.slice(0, 4)), rest = value.slice(5);
  let month = 1, day = 1;
  if (rest.startsWith("Q")) month = (Number(rest[1]) - 1) * 3 + 1;
  else if (rest.startsWith("H")) month = (Number(rest[1]) - 1) * 6 + 1;
  else if (rest) { const parts = rest.split("-"); month = Number(parts[0]); if (parts[1]) day = Number(parts[1]); }
  return Date.UTC(year, month - 1, day);
}
