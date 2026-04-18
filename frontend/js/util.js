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
