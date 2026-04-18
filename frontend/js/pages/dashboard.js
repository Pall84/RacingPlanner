import { api } from "../api.js";
import { renderCTLATLTSB, renderWeeklyVolume } from "../charts.js";
import { fmtTime } from "../util.js";

// Local: dashboard takes km already (from API aggregates), not m.
function fmtDist(km) { return `${km.toFixed(1)} km`; }

function tsbClass(tsb) {
  if (tsb === null || tsb === undefined) return "";
  if (tsb >= 5) return "green";
  if (tsb >= -10) return "";
  if (tsb >= -25) return "yellow";
  return "red";
}

function acwrClass(status) {
  if (status === "danger") return "red";
  if (status === "caution") return "yellow";
  return "green";
}

function readinessClass(score) {
  if (score >= 80) return "green";
  if (score >= 60) return "";
  if (score >= 40) return "yellow";
  return "red";
}

function workoutBadge(type) {
  if (!type) return "";
  return `<span class="badge badge-blue" style="font-size:11px">${type.replace(/_/g, " ")}</span>`;
}

export async function render(container) {
  const [summary, fitness, weekly] = await Promise.all([
    api.fitness.summary(),
    api.fitness.ctlAtlTsb(
      new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10),
      new Date().toISOString().slice(0, 10),
    ),
    api.fitness.weekly(12),
  ]);

  const tsbVal = summary.current_tsb;
  const tsbCls = tsbClass(tsbVal);

  container.innerHTML = `
    <h1 class="page-title">Dashboard</h1>

    <div class="cards-grid">
      <div class="card">
        <div class="card-label">Fitness (CTL)</div>
        <div class="card-value">${summary.current_ctl}</div>
        <div class="card-sub">42-day load avg</div>
      </div>
      <div class="card">
        <div class="card-label">Fatigue (ATL)</div>
        <div class="card-value">${summary.current_atl}</div>
        <div class="card-sub">7-day load avg</div>
      </div>
      <div class="card">
        <div class="card-label">Form (TSB)</div>
        <div class="card-value ${tsbCls}">${tsbVal >= 0 ? "+" : ""}${tsbVal}</div>
        <div class="card-sub">Fitness − Fatigue</div>
      </div>
      <div class="card">
        <div class="card-label">Est. VO2max</div>
        <div class="card-value">${summary.estimated_vo2max ?? "–"}</div>
        <div class="card-sub">mL/kg/min</div>
      </div>
      <div class="card">
        <div class="card-label">YTD Runs</div>
        <div class="card-value">${summary.ytd_runs}</div>
        <div class="card-sub">${new Date().getFullYear()}</div>
      </div>
      <div class="card">
        <div class="card-label">YTD Distance</div>
        <div class="card-value">${summary.ytd_distance_km}</div>
        <div class="card-sub">km</div>
      </div>
      <div class="card">
        <div class="card-label">ACWR</div>
        <div class="card-value ${acwrClass(summary.acwr_status)}">${summary.acwr ?? "–"}</div>
        <div class="card-sub">${summary.acwr_status === "danger" ? "High injury risk" : summary.acwr_status === "caution" ? "Elevated load" : "Balanced"}</div>
      </div>
      <div class="card">
        <div class="card-label">Readiness</div>
        <div class="card-value ${readinessClass(summary.readiness_score)}">${summary.readiness_score}</div>
        <div class="card-sub">${summary.readiness_label}${summary.readiness_sources?.includes("garmin") ? " (TSB+Garmin)" : ""}</div>
      </div>
      ${summary.garmin_latest ? `
      <div class="card">
        <div class="card-label">HRV</div>
        <div class="card-value">${summary.garmin_latest.hrv_last_night != null ? summary.garmin_latest.hrv_last_night.toFixed(0) : "–"}</div>
        <div class="card-sub">ms last night</div>
      </div>
      <div class="card">
        <div class="card-label">Resting HR</div>
        <div class="card-value">${summary.garmin_latest.resting_hr ?? "–"}</div>
        <div class="card-sub">bpm (watch)</div>
      </div>
      <div class="card">
        <div class="card-label">Body Battery</div>
        <div class="card-value ${(summary.garmin_latest.body_battery_latest ?? 50) >= 60 ? "green" : (summary.garmin_latest.body_battery_latest ?? 50) >= 30 ? "yellow" : "red"}">${summary.garmin_latest.body_battery_latest ?? "–"}</div>
        <div class="card-sub">0–100</div>
      </div>
      <div class="card">
        <div class="card-label">Sleep</div>
        <div class="card-value ${(summary.garmin_latest.sleep_hours ?? 7) >= 7 ? "green" : (summary.garmin_latest.sleep_hours ?? 7) >= 6 ? "yellow" : "red"}">${summary.garmin_latest.sleep_hours != null ? summary.garmin_latest.sleep_hours.toFixed(1) : "–"}</div>
        <div class="card-sub">hours</div>
      </div>
      ` : ""}
      <div class="card">
        <div class="card-label">Week Load</div>
        <div class="card-value">${summary.current_week_rss}</div>
        <div class="card-sub">target ${summary.recommended_load.min_rss}–${summary.recommended_load.max_rss}</div>
      </div>
    </div>

    <div class="grid-2col" style="margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Fitness / Fatigue / Form (90 days)</div>
        <div class="chart-wrap"><canvas id="ctl-chart" height="160"></canvas></div>
      </div>
      <div class="chart-section">
        <div class="chart-title">Weekly Volume (last 12 weeks)</div>
        <div class="chart-wrap"><canvas id="weekly-chart" height="160"></canvas></div>
      </div>
    </div>

    <div class="table-section">
      <div class="table-section-title">Recent Runs</div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Date</th><th>Name</th><th>Type</th><th>Dist</th><th>Time</th><th>Pace</th><th>HR</th><th>RSS</th>
          </tr>
        </thead>
        <tbody>
          ${summary.recent_activities.map((a) => `
            <tr onclick="navigate('/activity/${a.id}')">
              <td class="muted">${a.date || "–"}</td>
              <td>${a.name || "Run"}</td>
              <td>${workoutBadge(a.workout_type)}</td>
              <td>${fmtDist(a.distance_km)}</td>
              <td>${fmtTime(a.moving_time)}</td>
              <td>${a.avg_pace_str || "–"}</td>
              <td>${a.avg_hr ? Math.round(a.avg_hr) + " bpm" : "–"}</td>
              <td>${a.rss ?? "–"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;

  if (fitness.length > 0) renderCTLATLTSB("ctl-chart", fitness);
  if (weekly.length > 0) renderWeeklyVolume("weekly-chart", weekly);
}
