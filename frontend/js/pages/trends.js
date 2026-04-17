import { api } from "../api.js";
import { renderEFTrend, renderSleepEFScatter, renderHRVvdotOverlay, renderRHRvsRSS } from "../charts.js";

// Re-export Chart wrappers we need inline
function destroyChart(id) {
  const c = Chart.getChart(id);
  if (c) c.destroy();
}

function lineChart(canvasId, labels, datasets, yTitle = "", reverseY = false) {
  destroyChart(canvasId);
  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 12 }, grid: { color: "#2e3348" } },
        y: {
          reverse: reverseY,
          ticks: {
            color: "#8892a4",
            callback: reverseY ? (v) => {
              const m = Math.floor(v / 60), s = Math.round(v % 60);
              return `${m}:${String(s).padStart(2, "0")}`;
            } : undefined,
          },
          grid: { color: "#2e3348" },
          title: { display: !!yTitle, text: yTitle, color: "#8892a4" },
        },
      },
    },
  });
}

const WORKOUT_COLORS = {
  easy: "#3b82f6",
  moderate: "#8b5cf6",
  tempo: "#f59e0b",
  threshold: "#ef4444",
  vo2max_intervals: "#ec4899",
  long_run: "#6366f1",
  recovery: "#22c55e",
  race: "#f97316",
};

export async function render(container) {
  container.innerHTML = `<div class="loading-spinner">Loading trends…</div>`;

  const [weekly, ef, perf] = await Promise.all([
    api.fitness.weekly(52),
    api.fitness.aerobicEfficiency(52),
    api.fitness.performanceTrends(52),
  ]);

  const labels = weekly.map((w) => w.week_start.slice(5));
  const dist = weekly.map((w) => w.total_distance_km);
  const elev = weekly.map((w) => w.total_elevation);
  const rss = weekly.map((w) => w.total_rss);
  const monotony = weekly.map((w) => w.training_monotony);
  const strain = weekly.map((w) => w.training_strain);

  container.innerHTML = `
    <h1 class="page-title">Trends</h1>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Weekly Distance (52 weeks)</div>
        <canvas id="dist-trend" height="160"></canvas>
      </div>
      <div class="chart-section">
        <div class="chart-title">Weekly Elevation (52 weeks)</div>
        <canvas id="elev-trend" height="160"></canvas>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Training Load (RSS) per Week</div>
        <canvas id="rss-trend" height="160"></canvas>
      </div>
      <div class="chart-section">
        <div class="chart-title">Aerobic Efficiency Trend (EF)</div>
        <canvas id="ef-trend" height="160"></canvas>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Training Monotony</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Low variety → monotony > 1.5 (yellow zone) increases injury risk</div>
        <canvas id="monotony-trend" height="160"></canvas>
      </div>
      <div class="chart-section">
        <div class="chart-title">Training Strain</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Strain = Monotony × Weekly RSS. High strain with low form = overreaching</div>
        <canvas id="strain-trend" height="160"></canvas>
      </div>
    </div>

    <!-- Performance Trends -->
    <h2 style="font-size:18px;color:#e2e8f0;margin:32px 0 16px">Performance Trends</h2>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">VO2max Estimate (VDOT${perf.garmin_vo2max?.length ? " + Watch" : ""})</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Higher = better aerobic fitness. Weekly max from all runs ≥ 3km${perf.garmin_vo2max?.length ? " (green = VDOT, orange dashed = Garmin watch)" : ""}</div>
        <canvas id="vdot-trend" height="160"></canvas>
      </div>
      <div class="chart-section">
        <div class="chart-title">Pace Decoupling</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Lower = better. < 5% good, > 8% indicates poor aerobic endurance</div>
        <canvas id="decouple-trend" height="160"></canvas>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-section">
        <div class="chart-title">Pacing Consistency (CV%)</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Lower = more even pacing. High CV indicates inconsistent splits</div>
        <canvas id="pacing-trend" height="160"></canvas>
      </div>
      <div class="chart-section">
        <div class="chart-title">Aerobic Efficiency per Run</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Speed / HR ratio — higher is better. Colored by workout type</div>
        <canvas id="ef-scatter" height="160"></canvas>
      </div>
    </div>

    <!-- Health & Performance Correlations (Garmin) -->
    <div id="correlations-section" style="display:none">
      <h2 style="font-size:18px;color:#e2e8f0;margin:32px 0 16px">Health & Performance Correlations</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
        <div class="chart-section">
          <div class="chart-title">Sleep vs Efficiency Factor</div>
          <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Easy/moderate/long runs only — does more sleep improve efficiency?</div>
          <canvas id="sleep-ef-scatter" height="160"></canvas>
        </div>
        <div class="chart-section">
          <div class="chart-title">HRV vs VO2max (Weekly)</div>
          <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Do HRV improvements track with fitness gains?</div>
          <canvas id="hrv-vdot-chart" height="160"></canvas>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
        <div class="chart-section">
          <div class="chart-title">Resting HR vs Training Load</div>
          <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Rising RHR with high RSS may indicate insufficient recovery</div>
          <canvas id="rhr-rss-chart" height="160"></canvas>
        </div>
        <div class="chart-section" style="display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:13px;padding:24px">
          <div style="text-align:center">
            <div style="font-size:24px;margin-bottom:8px">📊</div>
            Correlations are computed from your training and Garmin health data over the last 26 weeks.
            More data = more reliable patterns.
          </div>
        </div>
      </div>
    </div>
  `;

  // Existing charts
  lineChart("dist-trend", labels, [{
    label: "km/week", data: dist, borderColor: "#4f7cff",
    backgroundColor: "rgba(79,124,255,0.08)", fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3,
  }], "km");

  lineChart("elev-trend", labels, [{
    label: "m elevation", data: elev, borderColor: "#7c3aed",
    backgroundColor: "rgba(124,58,237,0.08)", fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3,
  }], "m");

  lineChart("rss-trend", labels, [{
    label: "RSS", data: rss, borderColor: "#f97316",
    backgroundColor: "rgba(249,115,22,0.08)", fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3,
  }], "RSS");

  if (ef.length > 0) renderEFTrend("ef-trend", ef);

  lineChart("monotony-trend", labels, [{
    label: "Monotony", data: monotony, borderColor: "#eab308",
    backgroundColor: "rgba(234,179,8,0.08)", fill: true, borderWidth: 2, pointRadius: 2, tension: 0.3,
  }], "monotony");

  lineChart("strain-trend", labels, [{
    label: "Strain", data: strain, borderColor: "#ef4444",
    backgroundColor: "rgba(239,68,68,0.08)", fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3,
  }], "strain");

  // Performance trend charts
  if (perf.vo2max.length > 0) {
    const vLabels = perf.vo2max.map((p) => p.week_start.slice(5));
    const vData = perf.vo2max.map((p) => p.vdot);
    const datasets = [{
      label: "VDOT (calculated)", data: vData, borderColor: "#22c55e",
      backgroundColor: "rgba(34,197,94,0.08)", fill: true, borderWidth: 2, pointRadius: 3, tension: 0.3,
    }];
    // Overlay Garmin watch VO2max if available
    if (perf.garmin_vo2max && perf.garmin_vo2max.length > 0) {
      const garminMap = Object.fromEntries(perf.garmin_vo2max.map((g) => [g.week_start.slice(5), g.vo2max]));
      datasets.push({
        label: "Watch VO2max (Garmin)", data: vLabels.map((l) => garminMap[l] ?? null), borderColor: "#f59e0b",
        backgroundColor: "transparent", fill: false, borderWidth: 2, borderDash: [6, 4], pointRadius: 2, tension: 0.3, spanGaps: true,
      });
    }
    lineChart("vdot-trend", vLabels, datasets, "VO2max");
  }

  if (perf.pace_decoupling.length > 0) {
    const dLabels = perf.pace_decoupling.map((p) => p.week_start.slice(5));
    const dData = perf.pace_decoupling.map((p) => p.avg_decoupling);
    destroyChart("decouple-trend");
    new Chart(document.getElementById("decouple-trend"), {
      type: "line",
      data: {
        labels: dLabels,
        datasets: [{
          label: "Decouple %", data: dData, borderColor: "#f97316",
          backgroundColor: "rgba(249,115,22,0.08)", fill: true, borderWidth: 2, pointRadius: 2, tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { labels: { color: "#e2e8f0", boxWidth: 16 } },
          annotation: undefined,
        },
        scales: {
          x: { ticks: { color: "#8892a4", maxTicksLimit: 12 }, grid: { color: "#2e3348" } },
          y: {
            ticks: { color: "#8892a4" },
            grid: { color: "#2e3348" },
            title: { display: true, text: "%", color: "#8892a4" },
          },
        },
      },
      plugins: [{
        id: "refLines",
        beforeDraw(chart) {
          const { ctx, chartArea: { left, right }, scales: { y } } = chart;
          [
            { val: 5, color: "rgba(34,197,94,0.4)", label: "good" },
            { val: 8, color: "rgba(239,68,68,0.4)", label: "concern" },
          ].forEach(({ val, color, label }) => {
            const yPos = y.getPixelForValue(val);
            if (yPos >= chart.chartArea.top && yPos <= chart.chartArea.bottom) {
              ctx.save();
              ctx.strokeStyle = color;
              ctx.setLineDash([6, 4]);
              ctx.lineWidth = 1;
              ctx.beginPath();
              ctx.moveTo(left, yPos);
              ctx.lineTo(right, yPos);
              ctx.stroke();
              ctx.fillStyle = color;
              ctx.font = "11px sans-serif";
              ctx.fillText(label, right - 40, yPos - 4);
              ctx.restore();
            }
          });
        },
      }],
    });
  }

  if (perf.pacing_cv.length > 0) {
    const pLabels = perf.pacing_cv.map((p) => p.week_start.slice(5));
    const pData = perf.pacing_cv.map((p) => p.avg_pacing_cv);
    lineChart("pacing-trend", pLabels, [{
      label: "CV%", data: pData, borderColor: "#8b5cf6",
      backgroundColor: "rgba(139,92,246,0.08)", fill: true, borderWidth: 2, pointRadius: 2, tension: 0.3,
    }], "CV%");
  }

  // EF per-activity scatter
  if (perf.ef_per_activity.length > 0) {
    destroyChart("ef-scatter");
    const efData = perf.ef_per_activity;
    const efLabels = efData.map((p) => p.date.slice(5));
    const efValues = efData.map((p) => p.ef);
    const efColors = efData.map((p) => WORKOUT_COLORS[p.workout_type] || "#6b7280");

    new Chart(document.getElementById("ef-scatter"), {
      type: "scatter",
      data: {
        datasets: [{
          label: "EF",
          data: efData.map((p, i) => ({ x: i, y: p.ef })),
          backgroundColor: efColors,
          pointRadius: 4,
          pointHoverRadius: 6,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const p = efData[ctx.dataIndex];
                return `${p.date} · EF ${p.ef.toFixed(3)} · ${p.distance_km} km · ${(p.workout_type || "").replace(/_/g, " ")}`;
              },
            },
          },
        },
        scales: {
          x: {
            ticks: {
              color: "#8892a4",
              maxTicksLimit: 12,
              callback: (v) => efLabels[v] || "",
            },
            grid: { color: "#2e3348" },
          },
          y: {
            ticks: { color: "#8892a4" },
            grid: { color: "#2e3348" },
            title: { display: true, text: "EF (speed/HR)", color: "#8892a4" },
          },
        },
      },
    });
  }

  // Health & Performance Correlations (Garmin)
  try {
    const corr = await api.fitness.healthCorrelations(26);
    if (corr.sleep_ef.length > 0 || corr.hrv_vdot.length > 0 || corr.rhr_rss.length > 0) {
      const section = container.querySelector("#correlations-section");
      if (section) section.style.display = "block";
      if (corr.sleep_ef.length > 0) renderSleepEFScatter("sleep-ef-scatter", corr.sleep_ef);
      if (corr.hrv_vdot.length > 0) renderHRVvdotOverlay("hrv-vdot-chart", corr.hrv_vdot);
      if (corr.rhr_rss.length > 0) renderRHRvsRSS("rhr-rss-chart", corr.rhr_rss);
    }
  } catch (e) {
    // No Garmin data — correlations section stays hidden
  }
}
