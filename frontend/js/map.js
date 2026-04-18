import { escapeHtml } from "./util.js";

let _map = null;

// Zone colors: Z1 gray, Z2 blue, Z3 green, Z4 yellow-orange, Z5 red
const ZONE_COLORS = ["#9ca3af", "#60a5fa", "#4ade80", "#facc15", "#f87171"];
const ZONE_NAMES  = ["Z1 Recovery", "Z2 Aerobic", "Z3 Tempo", "Z4 Threshold", "Z5 VO2max"];

// Gradient: green → yellow → red  (norm 0=easy, 1=hard)
function gradientColor(norm) {
  if (norm < 0.5) {
    const t = norm / 0.5;
    return `rgb(${Math.round(34 + t * (234 - 34))},${Math.round(197 - t * (197 - 179))},${Math.round(94 - t * (94 - 8))})`;
  }
  const t = (norm - 0.5) / 0.5;
  return `rgb(${Math.round(234 + t * (239 - 234))},${Math.round(179 - t * (179 - 68))},${Math.round(8 + t * (68 - 8))})`;
}

function formatTooltip(value, type, zoneIdx) {
  if (type === "hr_zones") return `${ZONE_NAMES[zoneIdx]} · ${Math.round(value)} bpm`;
  if (type === "pace") {
    const m = Math.floor(value / 60), s = Math.round(value % 60);
    return `${m}:${String(s).padStart(2, "0")} /km`;
  }
  if (type === "hr") return `${Math.round(value)} bpm`;
  if (type === "cadence") return `${Math.round(value * 2)} spm`;
  if (type === "watts") return `${Math.round(value)} W`;
  if (type === "grade") return `${value.toFixed(1)}%`;
  return `${value.toFixed(1)}`;
}

/**
 * Render a GPS route on a Leaflet map with metric-based colour coding.
 *
 * @param {string}   mapId        - DOM element id
 * @param {Array}    latlngStream - [[lat, lng], ...]
 * @param {Array}    metricStream - parallel values (same length as latlng)
 * @param {string}   metricType   - "pace"|"hr"|"hr_zones"|"cadence"|"watts"|"grade"
 * @param {object}   opts
 *   opts.thresholds {number[4]} - HR zone upper boundaries [z1,z2,z3,z4] for "hr_zones" type
 */
export function renderActivityMap(
  mapId,
  latlngStream,
  metricStream = null,
  metricType = "pace",
  opts = {},
) {
  if (_map) {
    try { _map.remove(); } catch (_) { /* container may have been removed from DOM by SPA router */ }
    _map = null;
  }

  const el = document.getElementById(mapId);
  if (!el || !latlngStream || latlngStream.length < 2) {
    if (el) el.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#8892a4">No GPS data</div>`;
    return;
  }

  // Clear any stale Leaflet state on the container (handles SPA re-render of same mapId)
  delete el._leaflet_id;

  _map = L.map(mapId, { zoomControl: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(_map);

  const hasMetric = metricStream && metricStream.length === latlngStream.length;
  const n = latlngStream.length;

  if (hasMetric) {
    if (metricType === "hr_zones" && opts.thresholds && opts.thresholds.length >= 4) {
      // ── Zone-based colouring ──────────────────────────────────────────────
      const thr = opts.thresholds; // [z1_max, z2_max, z3_max, z4_max]
      for (let i = 0; i < n - 1; i++) {
        const hr = metricStream[i];
        if (!hr || hr <= 0) continue;
        let zi = 4;
        for (let z = 0; z < thr.length; z++) {
          if (hr <= thr[z]) { zi = z; break; }
        }
        L.polyline([latlngStream[i], latlngStream[i + 1]], {
          color: ZONE_COLORS[zi],
          weight: 5,
          opacity: 0.9,
        })
          .bindTooltip(formatTooltip(hr, "hr_zones", zi))
          .addTo(_map);
      }
    } else {
      // ── Continuous gradient colouring ─────────────────────────────────────
      const vals = metricStream.filter((v) => v !== null && v !== undefined && v > 0);
      if (vals.length === 0) {
        drawSolid(latlngStream);
      } else {
        const minV = Math.min(...vals);
        const maxV = Math.max(...vals);
        const range = maxV - minV || 1;

        for (let i = 0; i < n - 1; i++) {
          const v = metricStream[i];
          if (!v || v <= 0) continue;
          let norm = (v - minV) / range;
          // Pace (sec/km): slower = higher value = easier → invert so fast = hard = red
          if (metricType === "pace") norm = 1 - norm;
          L.polyline([latlngStream[i], latlngStream[i + 1]], {
            color: gradientColor(norm),
            weight: 5,
            opacity: 0.9,
          })
            .bindTooltip(formatTooltip(v, metricType, 0))
            .addTo(_map);
        }
      }
    }
  } else {
    drawSolid(latlngStream);
  }

  // Start / finish markers
  L.circleMarker(latlngStream[0], {
    radius: 7, fillColor: "#22c55e", color: "#fff", weight: 2, fillOpacity: 1,
  }).bindTooltip("Start").addTo(_map);
  L.circleMarker(latlngStream[n - 1], {
    radius: 7, fillColor: "#ef4444", color: "#fff", weight: 2, fillOpacity: 1,
  }).bindTooltip("Finish").addTo(_map);

  // Aid station markers (yellow triangles)
  const { aidStations = [], totalDistanceM = 0, latlngCumDist = [] } = opts;
  if (aidStations.length && n >= 2) {
    // Use pre-computed cumulative distances from full-resolution GPX when available.
    // This is exact — no downsampling error. Falls back to haversine + scaling.
    let cumDist;
    if (latlngCumDist.length === n) {
      cumDist = latlngCumDist;
    } else {
      // Fallback: compute haversine on downsampled latlng, then scale
      cumDist = [0];
      for (let i = 1; i < n; i++) {
        const [lat1, lon1] = latlngStream[i - 1];
        const [lat2, lon2] = latlngStream[i];
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) ** 2 +
          Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
        const d = 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        cumDist.push(cumDist[i - 1] + d);
      }
      const rawTotal = cumDist[n - 1];
      const scaleFactor = (totalDistanceM > 0 && rawTotal > 0) ? totalDistanceM / rawTotal : 1;
      if (scaleFactor !== 1) {
        for (let i = 1; i < n; i++) cumDist[i] *= scaleFactor;
      }
    }

    aidStations.forEach((as) => {
      let pos;
      if (as.lat != null && as.lon != null) {
        // Use stored geographic coordinates (from GPX waypoint) — most accurate
        // Project onto nearest track point so marker sits on the route line
        let bestI = 0, bestD = Infinity;
        const cosLat = Math.cos(as.lat * Math.PI / 180);
        for (let i = 0; i < n; i++) {
          const d = (latlngStream[i][0] - as.lat) ** 2 + ((latlngStream[i][1] - as.lon) * cosLat) ** 2;
          if (d < bestD) { bestD = d; bestI = i; }
        }
        pos = latlngStream[bestI];
      } else {
        // Fallback: position by distance along track (for manually added stations)
        const targetM = as.distance_km * 1000;
        let lo = 0, hi = n - 1;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (cumDist[mid] < targetM) lo = mid + 1;
          else hi = mid;
        }
        const idx = (lo > 0 && Math.abs(cumDist[lo - 1] - targetM) < Math.abs(cumDist[lo] - targetM)) ? lo - 1 : lo;
        pos = latlngStream[idx];
      }
      // Leaflet's bindPopup() renders HTML. Aid station name/notes are
      // user-provided (edited via the Course tab UI); unescaped, they allow
      // an attacker to run JS by naming their station `<img src=x onerror=...>`
      // and tricking someone into viewing the race. Escape both.
      const label = `<b>⛺ ${escapeHtml(as.name)}</b><br>${as.distance_km} km${
        as.notes ? "<br><span style='color:#ccc'>" + escapeHtml(as.notes) + "</span>" : ""
      }`;
      L.circleMarker(pos, {
        radius: 8,
        fillColor: "#facc15",
        color: "#1e2235",
        weight: 2,
        fillOpacity: 1,
      }).bindPopup(label).addTo(_map);
    });
  }

  _map.fitBounds(L.polyline(latlngStream).getBounds(), { padding: [20, 20] });

  function drawSolid(coords) {
    L.polyline(coords, { color: "#4f7cff", weight: 4, opacity: 0.85 }).addTo(_map);
  }
}

export function renderPolylineMap(mapId, polylineStr) {
  renderActivityMap(mapId, decodePolyline(polylineStr));
}

// Google encoded polyline decoder
function decodePolyline(encoded) {
  const result = [];
  let index = 0, lat = 0, lng = 0;
  while (index < encoded.length) {
    let b, shift = 0, rv = 0;
    do { b = encoded.charCodeAt(index++) - 63; rv |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += rv & 1 ? ~(rv >> 1) : rv >> 1;
    shift = 0; rv = 0;
    do { b = encoded.charCodeAt(index++) - 63; rv |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lng += rv & 1 ? ~(rv >> 1) : rv >> 1;
    result.push([lat / 1e5, lng / 1e5]);
  }
  return result;
}
