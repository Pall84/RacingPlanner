import { api } from "../api.js";
import { renderCTLATLTSB, renderWeeklyVolume, renderEFTrend, renderWorkoutDistribution, renderHRVTrend, renderRestingHRTrend, renderSleepTrend, renderBatteryStressTrend, renderRecoveryHistory } from "../charts.js";

export async function render(container) {
  container.innerHTML = `<div class="loading-spinner">Loading fitness data…</div>`;

  const [fitness, weekly, ef, vo2, prs, summary, dist] = await Promise.all([
    api.fitness.ctlAtlTsb(),
    api.fitness.weekly(52),
    api.fitness.aerobicEfficiency(26),
    api.fitness.vo2max(),
    api.fitness.personalRecords(),
    api.fitness.summary(),
    api.fitness.trainingDistribution(12),
  ]);

  const latest = fitness.length > 0 ? fitness[fitness.length - 1] : null;

  const prKeys = ["longest_run", "best_5k", "best_10k", "best_half_marathon", "best_marathon", "most_elevation", "fastest_pace"];
  const prLabels = { longest_run: "Longest Run", best_5k: "5k", best_10k: "10k", best_half_marathon: "Half Marathon", best_marathon: "Marathon", most_elevation: "Most Elevation", fastest_pace: "Fastest Pace" };

  // ACWR warning banner
  let acwrBanner = "";
  if (summary.acwr_status === "danger") {
    acwrBanner = `<div style="background:rgba(239,68,68,0.15);border:1px solid #ef4444;border-radius:8px;padding:12px 16px;margin-bottom:16px;color:#fca5a5">
      <strong>High Injury Risk</strong> — ACWR is ${summary.acwr} (>1.5). Consider reducing training load this week.
    </div>`;
  } else if (summary.acwr_status === "caution") {
    acwrBanner = `<div style="background:rgba(234,179,8,0.12);border:1px solid #eab308;border-radius:8px;padding:12px 16px;margin-bottom:16px;color:#fde047">
      <strong>Elevated Load</strong> — ACWR is ${summary.acwr} (>1.3). Monitor fatigue and recovery closely.
    </div>`;
  }

  container.innerHTML = `
    <h1 class="page-title">Fitness & Form</h1>

    ${acwrBanner}
    ${summary.garmin_latest ? (() => {
      const g = summary.garmin_latest;
      let alerts = "";
      if (g.sleep_hours != null && g.sleep_hours < 6)
        alerts += `<div style="background:rgba(234,179,8,0.12);border:1px solid #eab308;border-radius:8px;padding:10px 14px;margin-bottom:8px;color:#fde047;font-size:13px"><strong>Poor sleep</strong> (${g.sleep_hours}h) — consider easy training today</div>`;
      if (g.hrv_status === "LOW")
        alerts += `<div style="background:rgba(234,179,8,0.12);border:1px solid #eab308;border-radius:8px;padding:10px 14px;margin-bottom:8px;color:#fde047;font-size:13px"><strong>HRV below baseline</strong> — recovery may be compromised</div>`;
      if (g.body_battery_latest != null && g.body_battery_latest < 25)
        alerts += `<div style="background:rgba(234,179,8,0.12);border:1px solid #eab308;border-radius:8px;padding:10px 14px;margin-bottom:8px;color:#fde047;font-size:13px"><strong>Low body battery</strong> (${g.body_battery_latest}) — avoid high-intensity work</div>`;
      if (g.training_readiness != null && g.training_readiness < 30)
        alerts += `<div style="background:rgba(239,68,68,0.15);border:1px solid #ef4444;border-radius:8px;padding:10px 14px;margin-bottom:8px;color:#fca5a5;font-size:13px"><strong>Very low readiness</strong> (${Math.round(g.training_readiness)}) — rest day recommended</div>`;
      return alerts;
    })() : ""}

    <div class="cards-grid" style="margin-bottom:24px">
      <div class="card"><div class="card-label">Fitness (CTL)</div><div class="card-value">${latest?.ctl ?? "–"}</div><div class="card-sub">Chronic Training Load</div></div>
      <div class="card"><div class="card-label">Fatigue (ATL)</div><div class="card-value">${latest?.atl ?? "–"}</div><div class="card-sub">Acute Training Load</div></div>
      <div class="card">
        <div class="card-label">Form (TSB)</div>
        <div class="card-value ${latest && latest.tsb >= 5 ? 'green' : latest && latest.tsb < -25 ? 'red' : ''}">${latest?.tsb !== undefined ? (latest.tsb >= 0 ? "+" : "") + latest.tsb : "–"}</div>
        <div class="card-sub">Fitness − Fatigue</div>
      </div>
      <div class="card"><div class="card-label">Est. VO2max</div><div class="card-value">${vo2.estimated_vo2max ?? "–"}</div><div class="card-sub">mL/kg/min (VDOT)</div></div>
      <div class="card">
        <div class="card-label">ACWR</div>
        <div class="card-value ${summary.acwr_status === 'danger' ? 'red' : summary.acwr_status === 'caution' ? 'yellow' : 'green'}">${summary.acwr ?? "–"}</div>
        <div class="card-sub">Acute:Chronic ratio</div>
      </div>
      <div class="card">
        <div class="card-label">Readiness</div>
        <div class="card-value ${summary.readiness_score >= 80 ? 'green' : summary.readiness_score < 40 ? 'red' : summary.readiness_score < 60 ? 'yellow' : ''}">${summary.readiness_score}</div>
        <div class="card-sub">${summary.readiness_label}</div>
      </div>
      <div class="card">
        <div class="card-label">Week Load</div>
        <div class="card-value">${summary.current_week_rss}</div>
        <div class="card-sub">target ${summary.recommended_load.min_rss}–${summary.recommended_load.max_rss}</div>
      </div>
    </div>

    <div class="chart-section" style="margin-bottom:24px">
      <div class="chart-title">Fitness / Fatigue / Form (all time)</div>
      <div class="chart-wrap"><canvas id="ctl-full-chart" height="180"></canvas></div>
    </div>

    ${summary.garmin_connected ? `
    <div class="chart-section" style="margin-bottom:24px">
      <div class="chart-title">Recovery & Readiness (90 days)</div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Blended readiness from training load (TSB) + Garmin health data (HRV, sleep, body battery)</div>
      <div class="chart-wrap"><canvas id="recovery-history-chart" height="160"></canvas></div>
    </div>
    ` : ""}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Weekly Volume & CTL (52 weeks)</div>
        <canvas id="weekly-full-chart" height="180"></canvas>
      </div>
      <div class="chart-section">
        <div class="chart-title">Aerobic Efficiency Trend (26 weeks)</div>
        <canvas id="ef-chart" height="180"></canvas>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Workout Type Distribution (last ${dist.weeks} weeks)</div>
        <div style="max-width:420px;margin:0 auto"><canvas id="workout-dist-chart" height="220"></canvas></div>
      </div>
      <div class="chart-section">
        <div class="chart-title">Weekly Load Recommendation</div>
        <div style="padding:24px">
          <div style="margin-bottom:12px;font-size:14px;color:var(--muted)">Last 4-week average: <strong style="color:#e2e8f0">${summary.recommended_load.last_4wk_avg_rss} RSS</strong></div>
          <div style="margin-bottom:16px;font-size:14px;color:var(--muted)">Progressive overload target: <strong style="color:#22c55e">${summary.recommended_load.min_rss}–${summary.recommended_load.max_rss} RSS</strong> (+5-10%)</div>
          <div style="background:#2e3348;border-radius:8px;height:28px;position:relative;overflow:hidden">
            <div style="background:${summary.current_week_rss > summary.recommended_load.max_rss ? '#ef4444' : summary.current_week_rss >= summary.recommended_load.min_rss ? '#22c55e' : '#4f7cff'};height:100%;width:${Math.min(100, summary.recommended_load.max_rss > 0 ? (summary.current_week_rss / summary.recommended_load.max_rss * 100) : 0)}%;border-radius:8px;transition:width 0.5s"></div>
            <div style="position:absolute;top:4px;left:12px;font-size:13px;font-weight:600;color:#fff">${summary.current_week_rss} / ${summary.recommended_load.max_rss} RSS</div>
          </div>
          <div style="margin-top:8px;font-size:12px;color:var(--muted)">${summary.current_week_rss > summary.recommended_load.max_rss ? "Over target — consider an easy day" : summary.current_week_rss >= summary.recommended_load.min_rss ? "On track — good load balance" : "Room for more this week"}</div>
        </div>
      </div>
    </div>

    ${summary.garmin_connected ? `
    <!-- Garmin Health Trends -->
    <div class="chart-section" style="margin-bottom:24px">
      <div class="chart-title">Health & Recovery (Garmin)</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
        <div>
          <div style="font-size:13px;color:var(--muted);margin-bottom:8px">HRV Trend (90 days)</div>
          <canvas id="hrv-trend-chart" height="160"></canvas>
        </div>
        <div>
          <div style="font-size:13px;color:var(--muted);margin-bottom:8px">Resting HR Trend (90 days)</div>
          <canvas id="rhr-trend-chart" height="160"></canvas>
        </div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Sleep Duration (90 days)</div>
        <canvas id="sleep-trend-chart" height="160"></canvas>
      </div>
      <div class="chart-section">
        <div class="chart-title">Body Battery & Stress (90 days)</div>
        <canvas id="battery-stress-chart" height="160"></canvas>
      </div>
    </div>
    ` : ""}

    <!-- Personal Records -->
    <div class="chart-section" style="margin-bottom:24px">
      <div class="chart-title">Personal Records</div>
      <div class="cards-grid">
        ${prKeys.filter((k) => prs[k]).map((k) => `
          <div class="card" style="cursor:pointer" onclick="navigate('/activity/${prs[k].activity_id}')">
            <div class="card-label">${prLabels[k]}</div>
            <div class="card-value" style="font-size:20px">${prs[k].formatted}</div>
            <div class="card-sub">${prs[k].date}</div>
          </div>
        `).join("")}
      </div>
    </div>

    <!-- Weekly Training Load Table -->
    <div class="table-section">
      <div class="table-section-title">Weekly Summary (last 52 weeks)</div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Week</th><th>Runs</th><th>Distance</th><th>Time</th><th>Elevation</th>
            <th>RSS</th><th>CTL</th><th>Monotony</th><th>Strain</th><th>Sleep</th><th>RHR</th>
          </tr>
        </thead>
        <tbody>
          ${[...weekly].reverse().map((w) => `
            <tr>
              <td class="muted">${w.week_start}</td>
              <td>${w.run_count}</td>
              <td>${w.total_distance_km} km</td>
              <td>${Math.round(w.total_time_min)} min</td>
              <td>${w.total_elevation} m</td>
              <td>${w.total_rss}</td>
              <td>${w.avg_ctl}</td>
              <td class="${w.training_monotony > 1.5 ? 'yellow' : ''}">${w.training_monotony}</td>
              <td>${w.training_strain}</td>
              <td class="${w.avg_sleep_hours != null && w.avg_sleep_hours < 6 ? 'red' : w.avg_sleep_hours != null && w.avg_sleep_hours < 7 ? 'yellow' : ''}">${w.avg_sleep_hours != null ? w.avg_sleep_hours + "h" : "–"}</td>
              <td>${w.avg_resting_hr != null ? Math.round(w.avg_resting_hr) : "–"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;

  if (fitness.length > 0) renderCTLATLTSB("ctl-full-chart", fitness);
  if (weekly.length > 0) renderWeeklyVolume("weekly-full-chart", weekly);
  if (ef.length > 0) renderEFTrend("ef-chart", ef);
  if (dist.distribution.length > 0) renderWorkoutDistribution("workout-dist-chart", dist.distribution);

  // Garmin health charts (lazy-loaded only when connected)
  if (summary.garmin_connected) {
    try {
      const [trends, recoveryData] = await Promise.all([
        api.garmin.trends(90),
        api.fitness.recoveryHistory(90),
      ]);
      if (trends.dates && trends.dates.length > 0) {
        renderHRVTrend("hrv-trend-chart", trends.dates, trends.hrv);
        renderRestingHRTrend("rhr-trend-chart", trends.dates, trends.resting_hr);
        renderSleepTrend("sleep-trend-chart", trends.dates, trends.sleep_hours);
        renderBatteryStressTrend("battery-stress-chart", trends.dates, trends.body_battery, trends.stress);
      }
      if (recoveryData.days && recoveryData.days.length > 0) {
        renderRecoveryHistory("recovery-history-chart", recoveryData);
      }
    } catch (e) {
      console.warn("Garmin trends unavailable:", e);
    }
  }
}
