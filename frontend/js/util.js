/**
 * Shared frontend utilities.
 *
 * The escapeHtml helper protects against XSS when user-provided strings
 * (activity names, race names, aid station notes, etc.) are interpolated
 * into innerHTML templates. Rule of thumb:
 *
 *   - Prefer `element.textContent = user_string` where possible — you
 *     cannot forget it, because the API itself is safe.
 *   - Use `escapeHtml(user_string)` only where HTML must mix with data
 *     (Leaflet bindPopup, template literals with markup around text).
 *
 * The implementation leverages the browser's textContent-to-innerHTML
 * encoding — faster and more battle-tested than a regex replace.
 */
export function escapeHtml(s) {
  if (s == null) return "";
  const div = document.createElement("div");
  div.textContent = String(s);
  return div.innerHTML;
}

/**
 * Format a duration in seconds as `h:mm:ss` (if ≥1h) or `m:ss` otherwise.
 * Returns "–" for null/zero/falsy input. This is the canonical "run time"
 * formatter — was duplicated across 5 page files before this extract.
 */
export function fmtTime(sec) {
  if (!sec) return "–";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.round(sec % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Format a pace in seconds-per-km as `m:ss /km`.
 * Returns "–" for null/zero/falsy input.
 */
export function fmtPace(secPerKm) {
  if (!secPerKm) return "–";
  const m = Math.floor(secPerKm / 60);
  const s = Math.round(secPerKm % 60);
  return `${m}:${String(s).padStart(2, "0")} /km`;
}

/**
 * Client-side Minetti energy-cost-of-running formula.
 *
 * `gradeFrac` is slope as a decimal fraction (0.10 = 10% uphill). Returns
 * J/kg/m. Clamped to ±45% to mirror the backend's clamp in
 * app/analytics/metrics_engine.py::_minetti_cost (kept byte-for-byte
 * identical so per-sample client GAP matches stored per-activity avg GAP).
 */
function minettiCost(gradeFrac) {
  const g = Math.max(-0.45, Math.min(0.45, gradeFrac));
  return (
    155.4 * g ** 5
    - 30.4 * g ** 4
    - 43.3 * g ** 3
    + 46.3 * g ** 2
    + 19.5 * g
    + 3.6
  );
}

const FLAT_COST = 3.6;

/**
 * Convert raw velocity + grade streams into grade-adjusted velocity (m/s).
 * Returns null if input lengths don't match (caller should skip GAP overlay).
 * Mirrors compute_gap_speeds() in app/analytics/metrics_engine.py.
 */
export function computeGapVelocity(velArr, gradePctArr) {
  if (!velArr || !gradePctArr || velArr.length !== gradePctArr.length) {
    return null;
  }
  const n = velArr.length;
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const g = (gradePctArr[i] || 0) / 100;
    const cost = minettiCost(g);
    const effective = cost > 0 ? cost : FLAT_COST;
    out[i] = velArr[i] * (FLAT_COST / effective);
  }
  return out;
}
