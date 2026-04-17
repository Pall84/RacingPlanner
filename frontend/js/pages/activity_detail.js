import { api } from "../api.js";
import {
  renderZoneChart, renderHRStream, renderPaceStream,
  renderKmSplits, renderCadenceHistogram, renderPowerStream,
  renderProgressChart,
} from "../charts.js";
import { renderActivityMap } from "../map.js";

function fmtPace(sec) {
  if (!sec) return "–";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")} /km`;
}
function fmtTime(sec) {
  if (!sec) return "–";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h > 0 ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${m}:${String(s).padStart(2,"0")}`;
}
function fmtDist(m) { return m ? `${(m / 1000).toFixed(2)} km` : "–"; }
function decouplingBadge(pct) {
  if (pct === null || pct === undefined) return "–";
  if (pct < 5) return `<span class="badge badge-green">${pct.toFixed(1)}%</span>`;
  if (pct < 8) return `<span class="badge badge-yellow">${pct.toFixed(1)}%</span>`;
  return `<span class="badge badge-red">${pct.toFixed(1)}%</span>`;
}

export async function render(container, activityId) {
  container.innerHTML = `<div class="loading-spinner">Loading activity…</div>`;

  const [activity, streams, splits, laps, zones, recoveryCtx] = await Promise.all([
    api.activities.get(activityId),
    api.activities.streams(activityId),
    api.activities.kmSplits(activityId),
    api.activities.laps(activityId),
    api.activities.hrZones(activityId).catch(() => null),
    api.activities.recoveryContext(activityId).catch(() => null),
  ]);

  const date = (activity.start_date_local || activity.start_date || "").slice(0, 10);
  const timeStreams = streams.time || [];
  const velStreams = streams.velocity_smooth || [];
  const hrStreams = streams.heartrate || [];
  const cadStreams = streams.cadence || [];
  const wattsStreams = streams.watts || [];
  const latlng = streams.latlng || null;

  container.innerHTML = `
    <div style="margin-bottom:16px">
      <a href="/activities" class="back-link">← All Activities</a>
    </div>

    <div class="activity-header" style="display:flex;justify-content:space-between;align-items:flex-start">
      <div>
        <h1>${activity.name || "Run"}${activity.is_race ? ' <span style="font-size:0.65em;vertical-align:middle;background:#3b1f1f;color:#f87171;border:1px solid #5a2a2a;padding:2px 8px;border-radius:4px;margin-left:8px">🏁 RACE</span>' : ""}</h1>
        <div class="meta">${date} &nbsp;·&nbsp; ${activity.type || "Run"}${activity.workout_type ? ` &nbsp;·&nbsp; <span class="badge badge-blue">${activity.workout_type.replace(/_/g, " ")}</span>` : ""}${activity.treadmill_corrected ? ' &nbsp;·&nbsp; <span class="badge badge-yellow">treadmill corrected</span>' : ""}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">
        <button class="btn btn-sm" id="toggle-race-btn" title="Marking an activity as a race tells the predictor to use this performance as a ground-truth reference for future race predictions at similar distances.">
          ${activity.is_race ? "🏁 Unmark as Race" : "🏁 Mark as Race"}
        </button>
        <button class="btn btn-sm" id="refresh-activity-btn">↻ Refresh from Strava</button>
      </div>
    </div>

    <!-- Overview cards -->
    <div class="cards-grid" style="margin-bottom:24px">
      <div class="card"><div class="card-label">Distance</div><div class="card-value">${fmtDist(activity.distance_m)}</div></div>
      <div class="card"><div class="card-label">Moving Time</div><div class="card-value" style="font-size:18px">${fmtTime(activity.moving_time)}</div></div>
      <div class="card"><div class="card-label">Avg Pace</div><div class="card-value" style="font-size:20px">${fmtPace(activity.avg_pace_sec_per_km)}</div></div>
      <div class="card"><div class="card-label">GAP</div><div class="card-value" style="font-size:20px">${fmtPace(activity.avg_gap_sec_per_km)}</div></div>
      <div class="card"><div class="card-label">Avg HR</div><div class="card-value">${activity.average_heartrate ? Math.round(activity.average_heartrate) : "–"}<span style="font-size:14px;color:var(--muted)"> bpm</span></div></div>
      <div class="card"><div class="card-label">Elevation</div><div class="card-value">${Math.round(activity.elevation_gain || 0)}<span style="font-size:14px;color:var(--muted)"> m</span></div></div>
      <div class="card"><div class="card-label">TRIMP</div><div class="card-value">${activity.trimp ?? "–"}</div></div>
      <div class="card"><div class="card-label">RSS</div><div class="card-value">${activity.rss ?? "–"}</div></div>
      <div class="card"><div class="card-label">Decouple</div><div class="card-value" style="font-size:20px">${decouplingBadge(activity.pace_decoupling_pct)}</div></div>
      <div class="card"><div class="card-label">Cadence</div><div class="card-value">${activity.cadence_avg ? Math.round(activity.cadence_avg) : "–"}<span style="font-size:14px;color:var(--muted)"> spm</span></div></div>
      ${activity.normalized_power ? `<div class="card"><div class="card-label">NP</div><div class="card-value">${Math.round(activity.normalized_power)}<span style="font-size:14px;color:var(--muted)"> W</span></div></div>` : ""}
      ${activity.intensity_factor ? `<div class="card"><div class="card-label">IF</div><div class="card-value">${activity.intensity_factor.toFixed(2)}</div></div>` : ""}
    </div>

    ${recoveryCtx?.available ? `
    <!-- Recovery Context -->
    <div style="background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.15);border-radius:8px;padding:12px 16px;margin-bottom:24px">
      <div style="font-size:12px;color:#22c55e;font-weight:600;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px">Recovery Context — Morning of ${recoveryCtx.date}</div>
      <div class="cards-grid" style="grid-template-columns:repeat(6,1fr);gap:8px">
        <div class="card" style="padding:8px 10px"><div class="card-label" style="font-size:10px">Body Battery</div><div class="card-value" style="font-size:18px">${recoveryCtx.body_battery ?? "–"}</div></div>
        <div class="card" style="padding:8px 10px"><div class="card-label" style="font-size:10px">HRV</div><div class="card-value" style="font-size:18px">${recoveryCtx.hrv_last_night != null ? Math.round(recoveryCtx.hrv_last_night) : "–"}<span style="font-size:11px;color:var(--muted)"> ms</span></div></div>
        <div class="card" style="padding:8px 10px"><div class="card-label" style="font-size:10px">Sleep</div><div class="card-value" style="font-size:18px">${recoveryCtx.sleep_hours != null ? recoveryCtx.sleep_hours.toFixed(1) : "–"}<span style="font-size:11px;color:var(--muted)"> hrs</span></div></div>
        <div class="card" style="padding:8px 10px"><div class="card-label" style="font-size:10px">Resting HR</div><div class="card-value" style="font-size:18px">${recoveryCtx.resting_hr ?? "–"}<span style="font-size:11px;color:var(--muted)"> bpm</span></div></div>
        <div class="card" style="padding:8px 10px"><div class="card-label" style="font-size:10px">Stress</div><div class="card-value" style="font-size:18px">${recoveryCtx.stress_avg ?? "–"}</div></div>
        <div class="card" style="padding:8px 10px"><div class="card-label" style="font-size:10px">Readiness</div><div class="card-value" style="font-size:18px">${recoveryCtx.training_readiness != null ? Math.round(recoveryCtx.training_readiness) : "–"}</div></div>
      </div>
    </div>
    ` : ""}

    <!-- Map -->
    ${latlng ? `
      <div class="chart-section" style="margin-bottom:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div class="chart-title" style="margin-bottom:0">Route</div>
          <span id="map-metric-label" style="font-size:12px;color:var(--muted)"></span>
        </div>
        <div id="activity-map"></div>
      </div>
    ` : ""}

    <!-- Tabs -->
    <div class="tabs">
      <button class="tab-btn active" data-tab="hr-zones">HR Zones</button>
      <button class="tab-btn" data-tab="pace-gap">Pace & GAP</button>
      <button class="tab-btn" data-tab="cadence">Cadence</button>
      ${wattsStreams.length > 0 ? `<button class="tab-btn" data-tab="power">Power</button>` : ""}
      <button class="tab-btn" data-tab="splits">Splits</button>
      ${laps.length > 0 ? `<button class="tab-btn" data-tab="laps">Laps</button>` : ""}
      <button class="tab-btn" data-tab="compare">Compare</button>
    </div>

    <!-- HR Zones -->
    <div class="tab-panel active" id="tab-hr-zones">
      ${zones ? `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
          <div class="chart-section">
            <div class="chart-title">Time in Zone</div>
            <canvas id="zone-chart" height="180"></canvas>
          </div>
          <div class="chart-section">
            <div class="chart-title">Zone Breakdown</div>
            <table class="data-table">
              <thead><tr><th>Zone</th><th>Time</th><th>%</th><th>TRIMP</th></tr></thead>
              <tbody>
                ${["z1","z2","z3","z4","z5"].map((z, i) => {
                  const zd = zones[z];
                  const min = Math.floor((zd.seconds || 0) / 60);
                  const sec = (zd.seconds || 0) % 60;
                  return `<tr>
                    <td>${zd.name}</td>
                    <td>${min}:${String(sec).padStart(2,"0")}</td>
                    <td>${zd.percent}%</td>
                    <td>${zd.trimp.toFixed(1)}</td>
                  </tr>`;
                }).join("")}
                <tr style="border-top:1px solid var(--border);font-weight:600">
                  <td>Total</td><td colspan="2">${fmtTime(zones.total_seconds)}</td>
                  <td>${zones.total_trimp.toFixed(1)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        ${hrStreams.length > 0 ? `<div class="chart-section" style="margin-top:20px"><div class="chart-title">HR Stream</div><canvas id="hr-chart" height="120"></canvas></div>` : ""}
      ` : `<p style="color:var(--muted);padding:20px">No HR data available.</p>`}
    </div>

    <!-- Pace & GAP -->
    <div class="tab-panel" id="tab-pace-gap">
      ${velStreams.length > 0 ? `
        <div class="chart-section">
          <div class="chart-title">Pace over Time (blue = actual, green = grade-adjusted)</div>
          <canvas id="pace-chart" height="150"></canvas>
        </div>
        <div style="margin-top:12px;padding:12px 0;color:var(--muted);font-size:13px">
          Pace decoupling: ${decouplingBadge(activity.pace_decoupling_pct)}
          <span style="margin-left:16px">EF first half: ${activity.ef_first_half ? activity.ef_first_half.toFixed(5) : "–"}</span>
          <span style="margin-left:16px">EF second half: ${activity.ef_second_half ? activity.ef_second_half.toFixed(5) : "–"}</span>
        </div>
      ` : `<p style="color:var(--muted);padding:20px">No velocity stream available.</p>`}
    </div>

    <!-- Cadence -->
    <div class="tab-panel" id="tab-cadence">
      ${cadStreams.length > 0 ? `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
          <div class="chart-section">
            <div class="chart-title">Cadence Distribution (full SPM)</div>
            <canvas id="cad-hist" height="200"></canvas>
          </div>
          <div class="chart-section">
            <div class="chart-title">Cadence Stats</div>
            <div class="cards-grid" style="grid-template-columns:1fr 1fr">
              <div class="card"><div class="card-label">Avg SPM</div><div class="card-value">${activity.cadence_avg ? Math.round(activity.cadence_avg) : "–"}</div></div>
              <div class="card"><div class="card-label">CV%</div><div class="card-value" style="font-size:20px">${activity.cadence_cv_pct ? activity.cadence_cv_pct.toFixed(1) + "%" : "–"}</div></div>
              <div class="card"><div class="card-label">Stride Length</div><div class="card-value" style="font-size:20px">${activity.stride_length_avg_m ? activity.stride_length_avg_m.toFixed(2) + " m" : "–"}</div></div>
              <div class="card"><div class="card-label">Stride CV%</div><div class="card-value" style="font-size:20px">${activity.stride_length_cv_pct ? activity.stride_length_cv_pct.toFixed(1) + "%" : "–"}</div></div>
            </div>
          </div>
        </div>
      ` : `<p style="color:var(--muted);padding:20px">No cadence stream available.</p>`}
    </div>

    <!-- Power (optional) -->
    ${wattsStreams.length > 0 ? `
      <div class="tab-panel" id="tab-power">
        <div class="cards-grid" style="margin-bottom:16px">
          <div class="card"><div class="card-label">Norm. Power</div><div class="card-value">${activity.normalized_power ? Math.round(activity.normalized_power) + " W" : "–"}</div></div>
          <div class="card"><div class="card-label">Intensity Factor</div><div class="card-value">${activity.intensity_factor ? activity.intensity_factor.toFixed(2) : "–"}</div></div>
          <div class="card"><div class="card-label">Avg Power</div><div class="card-value">${activity.average_watts ? Math.round(activity.average_watts) + " W" : "–"}</div></div>
        </div>
        <div class="chart-section">
          <div class="chart-title">Power Stream</div>
          <canvas id="power-chart" height="150"></canvas>
        </div>
      </div>
    ` : ""}

    <!-- Splits -->
    <div class="tab-panel" id="tab-splits">
      ${splits.length > 0 ? `
        <div class="chart-section" style="margin-bottom:20px">
          <div class="chart-title">km Splits — Pace & HR</div>
          <canvas id="splits-chart" height="160"></canvas>
        </div>
        <div class="table-section">
          <table class="data-table">
            <thead><tr><th>km</th><th>Pace</th><th>GAP</th><th>HR</th><th>Cadence</th><th>Elev +</th><th>Elev −</th><th>Grade</th></tr></thead>
            <tbody>
              ${splits.map((s) => `<tr>
                <td>${s.km_index}</td>
                <td>${s.pace_str || "–"}</td>
                <td>${s.gap_str || "–"}</td>
                <td>${s.avg_hr ? Math.round(s.avg_hr) + " bpm" : "–"}</td>
                <td>${s.avg_cadence ? Math.round(s.avg_cadence) + " spm" : "–"}</td>
                <td>${s.elevation_gain ? "+" + Math.round(s.elevation_gain) + " m" : "–"}</td>
                <td>${s.elevation_loss ? "−" + Math.round(s.elevation_loss) + " m" : "–"}</td>
                <td>${s.avg_grade_pct !== null && s.avg_grade_pct !== undefined ? s.avg_grade_pct.toFixed(1) + "%" : "–"}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      ` : `<p style="color:var(--muted);padding:20px">No split data available.</p>`}
    </div>

    <!-- Laps (populated by initLapsTab after DOM is set) -->
    ${laps.length > 0 ? `<div class="tab-panel" id="tab-laps"></div>` : ""}

    <!-- Compare tab (lazy loaded) -->
    <div class="tab-panel" id="tab-compare">
      <div class="loading-spinner" id="compare-loading">Loading similar runs…</div>
      <div id="compare-content" style="display:none"></div>
    </div>
  `;

  // Derived stream for pace coloring (sec/km)
  const paceStream = velStreams.length
    ? velStreams.map((v) => (v > 0.3 ? 1000 / v : null))
    : null;

  // Update map colour coding to match the active tab
  function updateMap(tabId) {
    if (!latlng) return;
    const label = container.querySelector("#map-metric-label");
    const tab = {
      "hr-zones": hrStreams.length && zones?.zone_thresholds
        ? { stream: hrStreams, type: "hr_zones", opts: { thresholds: zones.zone_thresholds }, text: "HR Zones" }
        : { stream: paceStream, type: "pace", opts: {}, text: "Pace" },
      "pace-gap": { stream: paceStream, type: "pace", opts: {}, text: "Pace" },
      "cadence":  cadStreams.length
        ? { stream: cadStreams, type: "cadence", opts: {}, text: "Cadence" }
        : { stream: paceStream, type: "pace", opts: {}, text: "Pace" },
      "power": wattsStreams.length
        ? { stream: wattsStreams, type: "watts", opts: {}, text: "Power" }
        : { stream: paceStream, type: "pace", opts: {}, text: "Pace" },
      "splits": { stream: paceStream, type: "pace", opts: {}, text: "Pace" },
      "laps":   { stream: paceStream, type: "pace", opts: {}, text: "Pace" },
    };
    const cfg = tab[tabId] || tab["pace-gap"];
    if (label) label.textContent = `colored by ${cfg.text}`;
    renderActivityMap("activity-map", latlng, cfg.stream, cfg.type, cfg.opts);
  }

  // Initial map render (default active tab is HR Zones)
  updateMap("hr-zones");

  if (zones) {
    renderZoneChart("zone-chart", zones);
    if (hrStreams.length > 0) renderHRStream("hr-chart", timeStreams, hrStreams);
  }

  if (velStreams.length > 0) {
    renderPaceStream("pace-chart", timeStreams, velStreams);
  }

  if (cadStreams.length > 0) {
    renderCadenceHistogram("cad-hist", cadStreams);
  }

  if (wattsStreams.length > 0) {
    renderPowerStream("power-chart", timeStreams, wattsStreams);
  }

  if (splits.length > 0) {
    renderKmSplits("splits-chart", splits);
  }

  // Refresh from Strava
  container.querySelector("#refresh-activity-btn")?.addEventListener("click", async (e) => {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = "Refreshing…";
    try {
      await api.activities.refresh(activityId);
      await render(container, activityId);  // re-render with fresh data
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "↻ Refresh from Strava";
      alert("Refresh failed: " + err.message);
    }
  });

  // Toggle race flag — updates the button + re-renders so the badge appears/disappears
  container.querySelector("#toggle-race-btn")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Saving…";
    try {
      const next = !activity.is_race;
      await api.activities.setRaceFlag(activityId, next);
      await render(container, activityId);  // re-render with the updated flag
    } catch (err) {
      btn.disabled = false;
      btn.textContent = original;
      alert("Could not update race flag: " + err.message);
    }
  });

  // Tab switching — also updates map colour coding
  container.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      container.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      container.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const tabId = btn.dataset.tab;
      const panel = container.querySelector(`#tab-${tabId}`);
      if (panel) panel.classList.add("active");
      updateMap(tabId);

      // Lazy-load Compare tab
      if (tabId === "compare" && !container._compareLoaded) {
        container._compareLoaded = true;
        initCompareTab(container, activityId);
      }
    });
  });

  // Laps tab with treadmill correction edit mode
  if (laps.length > 0) {
    initLapsTab(container, laps, activityId);
  }
}


function initLapsTab(container, initialLaps, activityId) {
  const el = container.querySelector("#tab-laps");
  if (!el) return;

  let lapData = initialLaps.slice();
  let editMode = false;

  function renderTable() {
    el.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <span style="color:var(--muted);font-size:13px">${lapData.length} laps</span>
        <div>
          ${editMode
            ? `<button class="btn btn-sm" id="save-all-laps">Save All</button>
               <button class="btn btn-sm" id="cancel-edit-laps" style="margin-left:8px">Cancel</button>`
            : `<button class="btn btn-sm" id="edit-laps">Edit Corrections</button>`}
        </div>
      </div>
      <div class="table-section">
        <table class="data-table">
          <thead>
            <tr>
              <th>#</th><th>Name</th>
              <th>Distance${editMode ? " (km)" : ""}</th>
              <th>Pace</th><th>HR</th><th>Cadence</th>
              <th>Elevation${editMode ? " (m)" : ""}</th>
              ${editMode ? "<th></th>" : ""}
            </tr>
          </thead>
          <tbody>
            ${lapData.map((l) => {
              const effKm = l.effective_distance != null ? (l.effective_distance / 1000) : null;
              const origKm = l.distance != null ? (l.distance / 1000) : null;
              const effElev = l.effective_elevation_gain;
              const origElev = l.total_elevation_gain;
              if (editMode) {
                return `<tr data-lap-index="${l.lap_index}">
                  <td>${l.lap_index}</td>
                  <td class="muted">${l.name || "–"}</td>
                  <td>
                    <input type="number" class="form-input lap-dist-input"
                      value="${effKm != null ? effKm.toFixed(3) : ""}"
                      step="0.001" min="0.001" style="width:88px">
                    ${l.corrected_distance != null
                      ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">Strava: ${origKm != null ? origKm.toFixed(2) : "–"} km</div>`
                      : ""}
                  </td>
                  <td>${l.pace_str || "–"}</td>
                  <td>${l.average_heartrate ? Math.round(l.average_heartrate) + " bpm" : "–"}</td>
                  <td>${l.average_cadence_spm ? Math.round(l.average_cadence_spm) + " spm" : "–"}</td>
                  <td>
                    <input type="number" class="form-input lap-elev-input"
                      value="${effElev != null ? Math.round(effElev) : ""}"
                      step="1" min="0" style="width:72px">
                    ${l.corrected_elevation_gain != null
                      ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">Strava: ${origElev != null ? Math.round(origElev) : "–"} m</div>`
                      : ""}
                  </td>
                  <td style="white-space:nowrap">
                    <button class="btn btn-sm save-lap-btn">Save</button>
                    ${l.is_corrected
                      ? `<button class="btn btn-sm reset-lap-btn" style="margin-left:4px">Reset</button>`
                      : ""}
                  </td>
                </tr>`;
              }
              return `<tr>
                <td>${l.lap_index}</td>
                <td class="muted">${l.name || "–"}</td>
                <td>
                  ${effKm != null ? effKm.toFixed(2) + " km" : "–"}
                  ${l.is_corrected && l.corrected_distance != null
                    ? `<span class="badge badge-yellow" style="font-size:10px;margin-left:4px;vertical-align:middle">corrected</span>`
                    : ""}
                </td>
                <td>${l.pace_str || "–"}</td>
                <td>${l.average_heartrate ? Math.round(l.average_heartrate) + " bpm" : "–"}</td>
                <td>${l.average_cadence_spm ? Math.round(l.average_cadence_spm) + " spm" : "–"}</td>
                <td>
                  ${effElev != null ? "+" + Math.round(effElev) + " m" : "–"}
                  ${l.is_corrected && l.corrected_elevation_gain != null
                    ? `<span class="badge badge-yellow" style="font-size:10px;margin-left:4px;vertical-align:middle">corrected</span>`
                    : ""}
                </td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>
      </div>
    `;
    wireEvents();
  }

  function wireEvents() {
    el.querySelector("#edit-laps")?.addEventListener("click", () => {
      editMode = true;
      renderTable();
    });
    el.querySelector("#cancel-edit-laps")?.addEventListener("click", () => {
      editMode = false;
      renderTable();
    });
    el.querySelector("#save-all-laps")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "Saving…";
      for (const row of el.querySelectorAll("tr[data-lap-index]")) {
        await saveLapRow(row);
      }
      editMode = false;
      renderTable();
    });
    el.querySelectorAll(".save-lap-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const row = e.target.closest("tr[data-lap-index]");
        row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
        await saveLapRow(row);
        renderTable();
      });
    });
    el.querySelectorAll(".reset-lap-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const row = e.target.closest("tr[data-lap-index]");
        const lapIdx = parseInt(row.dataset.lapIndex);
        row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
        try {
          const updated = await api.activities.updateLap(activityId, lapIdx, {
            corrected_distance_km: null,
            corrected_elevation_gain: null,
          });
          const i = lapData.findIndex((l) => l.lap_index === lapIdx);
          if (i >= 0) lapData[i] = updated;
        } catch (err) {
          alert("Failed to reset lap: " + err.message);
        }
        renderTable();
      });
    });
  }

  async function saveLapRow(row) {
    const lapIdx = parseInt(row.dataset.lapIndex);
    const distVal = row.querySelector(".lap-dist-input")?.value.trim();
    const elevVal = row.querySelector(".lap-elev-input")?.value.trim();
    const body = {
      corrected_distance_km: distVal !== "" && distVal != null ? parseFloat(distVal) : null,
      corrected_elevation_gain: elevVal !== "" && elevVal != null ? parseFloat(elevVal) : null,
    };
    try {
      const updated = await api.activities.updateLap(activityId, lapIdx, body);
      const i = lapData.findIndex((l) => l.lap_index === lapIdx);
      if (i >= 0) lapData[i] = updated;
    } catch (err) {
      alert("Failed to save lap " + lapIdx + ": " + err.message);
    }
  }

  renderTable();
}


async function initCompareTab(container, activityId) {
  const loading = container.querySelector("#compare-loading");
  const content = container.querySelector("#compare-content");

  try {
    const data = await api.activities.similar(activityId);
    loading.style.display = "none";
    content.style.display = "block";

    if (!data.similar_runs || data.similar_runs.length === 0) {
      content.innerHTML = `
        <div style="text-align:center;padding:40px;color:var(--muted)">
          <div style="font-size:48px;margin-bottom:12px">🔍</div>
          <div>No similar runs found.</div>
          <div style="font-size:13px;margin-top:8px">Similar runs have the same workout type and distance within ±15%.</div>
        </div>
      `;
      return;
    }

    function deltaCell(val, unit, lowerIsBetter = true) {
      if (val === undefined || val === null) return `<td class="muted">–</td>`;
      const better = lowerIsBetter ? val < 0 : val > 0;
      const cls = better ? "green" : val === 0 ? "" : "red";
      const sign = val > 0 ? "+" : "";
      if (unit === "pace") {
        const absSec = Math.abs(val);
        const m = Math.floor(absSec / 60), s = Math.round(absSec % 60);
        const formatted = m > 0 ? `${sign}${val < 0 ? "-" : ""}${m}:${String(s).padStart(2, "0")}` : `${sign}${val.toFixed(0)}s`;
        return `<td class="${cls}">${formatted}</td>`;
      }
      return `<td class="${cls}">${sign}${typeof val === "number" ? val.toFixed(1) : val}${unit}</td>`;
    }

    content.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
        <div class="chart-section">
          <div class="chart-title">Similar Runs</div>
          <div style="max-height:400px;overflow-y:auto">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Date</th><th>Name</th><th>Dist</th><th>Pace</th><th>Δ Pace</th>
                  <th>HR</th><th>Δ HR</th><th>EF</th><th>Decouple</th>
                </tr>
              </thead>
              <tbody>
                ${data.similar_runs.map((r) => `
                  <tr style="cursor:pointer" onclick="navigate('/activity/${r.id}')">
                    <td class="muted">${r.date}</td>
                    <td>${r.name || "Run"}</td>
                    <td>${r.distance_km} km</td>
                    <td>${r.avg_pace_str || "–"}</td>
                    ${deltaCell(r.delta_pace, "pace", true)}
                    <td>${r.average_heartrate ? Math.round(r.average_heartrate) : "–"}</td>
                    ${deltaCell(r.delta_hr, " bpm", true)}
                    <td>${r.ef_first_half ? r.ef_first_half.toFixed(3) : "–"}</td>
                    <td>${r.pace_decoupling_pct !== null ? r.pace_decoupling_pct + "%" : "–"}</td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        </div>
        <div class="chart-section">
          <div class="chart-title">Progress Over Time</div>
          <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Pace (blue, lower = faster) and HR (red) for similar runs</div>
          <canvas id="progress-chart" height="200"></canvas>
        </div>
      </div>
    `;

    renderProgressChart("progress-chart", data.similar_runs, data.reference);
  } catch (err) {
    loading.style.display = "none";
    content.style.display = "block";
    content.innerHTML = `<div style="color:var(--muted);text-align:center;padding:32px">Failed to load similar runs: ${err.message}</div>`;
  }
}
