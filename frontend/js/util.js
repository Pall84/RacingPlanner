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
