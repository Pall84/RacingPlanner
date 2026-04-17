// Chart.js wrapper helpers — all return the Chart instance

const ZONE_COLORS = ["#6b7280", "#3b82f6", "#22c55e", "#f59e0b", "#ef4444"];
const ZONE_NAMES = ["Z1 Recovery", "Z2 Aerobic", "Z3 Tempo", "Z4 Threshold", "Z5 VO2max"];

function destroyIfExists(canvasId) {
  const existing = Chart.getChart(canvasId);
  if (existing) existing.destroy();
}

export function renderZoneChart(canvasId, zoneData) {
  destroyIfExists(canvasId);
  const zones = ["z1", "z2", "z3", "z4", "z5"];
  const labels = ZONE_NAMES;
  const minutes = zones.map((z) => Math.round((zoneData[z]?.seconds || 0) / 60));
  const pcts = zones.map((z) => zoneData[z]?.percent || 0);

  return new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: minutes,
        backgroundColor: ZONE_COLORS,
        borderRadius: 4,
        barThickness: 22,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const z = zones[ctx.dataIndex];
              return ` ${minutes[ctx.dataIndex]}min  (${pcts[ctx.dataIndex]}%)  TRIMP: ${(zoneData[z]?.trimp || 0).toFixed(1)}`;
            },
          },
        },
      },
      scales: {
        x: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "minutes", color: "#8892a4" } },
        y: { ticks: { color: "#e2e8f0" }, grid: { display: false } },
      },
    },
  });
}

export function renderHRStream(canvasId, timeArr, hrArr, zoneBounds = null) {
  destroyIfExists(canvasId);
  // Downsample to max 500 points for performance
  const step = Math.max(1, Math.floor(timeArr.length / 500));
  const t = timeArr.filter((_, i) => i % step === 0).map((v) => Math.round(v / 60));
  const hr = hrArr.filter((_, i) => i % step === 0);

  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: {
      labels: t,
      datasets: [{
        label: "Heart Rate",
        data: hr,
        borderColor: "#ef4444",
        backgroundColor: "rgba(239,68,68,0.08)",
        fill: true,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 8 }, grid: { color: "#2e3348" }, title: { display: true, text: "min", color: "#8892a4" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "bpm", color: "#8892a4" } },
      },
    },
  });
}

export function renderPaceStream(canvasId, timeArr, velArr, gapVelArr = null) {
  destroyIfExists(canvasId);
  const step = Math.max(1, Math.floor(timeArr.length / 500));
  const t = timeArr.filter((_, i) => i % step === 0).map((v) => Math.round(v / 60));

  // Convert m/s → sec/km (invert for display)
  const paceFromVel = (v) => (v > 0.3 ? 1000 / v : null);
  const pace = velArr.filter((_, i) => i % step === 0).map(paceFromVel);

  const datasets = [{
    label: "Pace",
    data: pace,
    borderColor: "#4f7cff",
    borderWidth: 1.5,
    pointRadius: 0,
    tension: 0.3,
    fill: false,
  }];

  if (gapVelArr) {
    const gap = gapVelArr.filter((_, i) => i % step === 0).map(paceFromVel);
    datasets.push({
      label: "GAP",
      data: gap,
      borderColor: "#22c55e",
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.3,
      borderDash: [4, 4],
      fill: false,
    });
  }

  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels: t, datasets },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 8 }, grid: { color: "#2e3348" }, title: { display: true, text: "min", color: "#8892a4" } },
        y: {
          reverse: true,
          ticks: {
            color: "#8892a4",
            callback: (v) => {
              if (!v) return "";
              const m = Math.floor(v / 60), s = Math.round(v % 60);
              return `${m}:${String(s).padStart(2, "0")}`;
            },
          },
          grid: { color: "#2e3348" },
          title: { display: true, text: "pace /km", color: "#8892a4" },
        },
      },
    },
  });
}

export function renderCTLATLTSB(canvasId, data) {
  destroyIfExists(canvasId);
  const labels = data.map((d) => d.date);
  const ctl = data.map((d) => d.ctl);
  const atl = data.map((d) => d.atl);
  const tsb = data.map((d) => d.tsb);

  return new Chart(document.getElementById(canvasId), {
    data: {
      labels,
      datasets: [
        {
          type: "line",
          label: "Fitness (CTL)",
          data: ctl,
          borderColor: "#4f7cff",
          backgroundColor: "rgba(79,124,255,0.08)",
          fill: true,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "Fatigue (ATL)",
          data: atl,
          borderColor: "#ef4444",
          backgroundColor: "rgba(239,68,68,0.05)",
          fill: true,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y",
        },
        {
          type: "bar",
          label: "Form (TSB)",
          data: tsb,
          backgroundColor: tsb.map((v) => (v >= 0 ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)")),
          borderWidth: 0,
          yAxisID: "y2",
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#e2e8f0", boxWidth: 16 } },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1)}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 12 }, grid: { color: "#2e3348" } },
        y: {
          position: "left",
          ticks: { color: "#8892a4" },
          grid: { color: "#2e3348" },
          title: { display: true, text: "CTL / ATL", color: "#8892a4" },
        },
        y2: {
          position: "right",
          ticks: { color: "#8892a4" },
          grid: { display: false },
          title: { display: true, text: "TSB (Form)", color: "#8892a4" },
        },
      },
    },
  });
}

export function renderWeeklyVolume(canvasId, weeks) {
  destroyIfExists(canvasId);
  const labels = weeks.map((w) => w.week_start.slice(5)); // MM-DD
  const dist = weeks.map((w) => w.total_distance_km);
  const ctl = weeks.map((w) => w.avg_ctl);

  return new Chart(document.getElementById(canvasId), {
    data: {
      labels,
      datasets: [
        {
          type: "bar",
          label: "Weekly km",
          data: dist,
          backgroundColor: "rgba(79,124,255,0.6)",
          borderRadius: 3,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "Fitness (CTL)",
          data: ctl,
          borderColor: "#22c55e",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
          yAxisID: "y2",
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 16 }, grid: { color: "#2e3348" } },
        y: { position: "left", ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "km", color: "#8892a4" } },
        y2: { position: "right", ticks: { color: "#8892a4" }, grid: { display: false }, title: { display: true, text: "CTL", color: "#8892a4" } },
      },
    },
  });
}

export function renderKmSplits(canvasId, splits) {
  destroyIfExists(canvasId);
  const labels = splits.map((s) => `${s.km_index}km`);
  const paces = splits.map((s) => s.pace_sec_per_km);
  const gaps = splits.map((s) => s.gap_sec_per_km);
  const hrs = splits.map((s) => s.avg_hr);

  return new Chart(document.getElementById(canvasId), {
    data: {
      labels,
      datasets: [
        {
          type: "bar",
          label: "Pace",
          data: paces,
          backgroundColor: paces.map((p) => `rgba(79,124,255,0.6)`),
          borderRadius: 3,
          yAxisID: "y",
        },
        {
          type: "bar",
          label: "GAP",
          data: gaps,
          backgroundColor: "rgba(34,197,94,0.4)",
          borderRadius: 3,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "HR",
          data: hrs,
          borderColor: "#ef4444",
          borderWidth: 2,
          pointRadius: 3,
          fill: false,
          yAxisID: "y2",
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" } },
        y: {
          reverse: true,
          position: "left",
          ticks: {
            color: "#8892a4",
            callback: (v) => {
              const m = Math.floor(v / 60), s = Math.round(v % 60);
              return `${m}:${String(s).padStart(2, "0")}`;
            },
          },
          grid: { color: "#2e3348" },
          title: { display: true, text: "pace /km", color: "#8892a4" },
        },
        y2: {
          position: "right",
          ticks: { color: "#8892a4" },
          grid: { display: false },
          title: { display: true, text: "bpm", color: "#8892a4" },
        },
      },
    },
  });
}

export function renderCadenceHistogram(canvasId, cadenceStream) {
  destroyIfExists(canvasId);
  const full = cadenceStream.map((v) => v * 2).filter((v) => v > 100 && v < 220);
  if (full.length === 0) return;

  const bins = [];
  for (let b = 140; b <= 200; b += 5) bins.push(b);
  const counts = bins.map((b) => full.filter((v) => v >= b && v < b + 5).length);

  return new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: {
      labels: bins.map((b) => `${b}`),
      datasets: [{
        label: "Steps/min",
        data: counts,
        backgroundColor: "rgba(79,124,255,0.6)",
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "SPM", color: "#8892a4" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "samples", color: "#8892a4" } },
      },
    },
  });
}

export function renderEFTrend(canvasId, efData) {
  destroyIfExists(canvasId);
  const labels = efData.map((d) => d.week_start.slice(5));
  const ef = efData.map((d) => d.avg_ef ? +d.avg_ef.toFixed(5) : null);

  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Efficiency Factor (speed/HR)",
        data: ef,
        borderColor: "#22c55e",
        backgroundColor: "rgba(34,197,94,0.08)",
        fill: true,
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 12 }, grid: { color: "#2e3348" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "EF", color: "#8892a4" } },
      },
    },
  });
}

export function renderPowerStream(canvasId, timeArr, wattsArr) {
  destroyIfExists(canvasId);
  const step = Math.max(1, Math.floor(timeArr.length / 500));
  const t = timeArr.filter((_, i) => i % step === 0).map((v) => Math.round(v / 60));
  const w = wattsArr.filter((_, i) => i % step === 0);

  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: {
      labels: t,
      datasets: [{
        label: "Power (W)",
        data: w,
        borderColor: "#f59e0b",
        backgroundColor: "rgba(245,158,11,0.08)",
        fill: true,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.2,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 8 }, grid: { color: "#2e3348" }, title: { display: true, text: "min", color: "#8892a4" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "watts", color: "#8892a4" } },
      },
    },
  });
}

// Interpolate elevation at a given distance (metres) from the elevation profile array.
function _eleAtDist(profile, distM) {
  for (let i = 0; i < profile.length - 1; i++) {
    if (profile[i][0] <= distM && profile[i + 1][0] >= distM) {
      const t = (distM - profile[i][0]) / (profile[i + 1][0] - profile[i][0]);
      return profile[i][1] + t * (profile[i + 1][1] - profile[i][1]);
    }
  }
  return profile[profile.length - 1][1];
}

export function renderElevationProfile(canvasId, elevationProfile, aidStations = []) {
  destroyIfExists(canvasId);
  if (!elevationProfile || elevationProfile.length < 2) return null;

  // Use {x, y} format so aid station scatter points can be placed at precise distances
  const lineData = elevationProfile.map(([d, e]) => ({ x: d / 1000, y: e }));
  const eleValues = elevationProfile.map(([, e]) => e);
  const minEle = Math.min(...eleValues);
  const maxEle = Math.max(...eleValues);

  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const ctx = canvas.getContext("2d");

  // Gradient fill: green (low) → tan → brown (high)
  const grad = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 300);
  grad.addColorStop(0, "rgba(139, 90, 43, 0.85)");
  grad.addColorStop(0.5, "rgba(180, 140, 80, 0.6)");
  grad.addColorStop(1, "rgba(74, 163, 110, 0.35)");

  // Aid station scatter points (interpolated elevation at each station's distance)
  const stationScatter = aidStations.map((as) => ({
    x: as.distance_km,
    y: _eleAtDist(elevationProfile, as.distance_km * 1000),
    name: as.name,
    notes: as.notes,
  }));

  return new Chart(canvas, {
    data: {
      datasets: [
        {
          type: "line",
          data: lineData,
          fill: true,
          backgroundColor: grad,
          borderColor: "#b8860b",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        },
        ...(stationScatter.length ? [{
          type: "scatter",
          label: "Aid Stations",
          data: stationScatter,
          backgroundColor: "#facc15",
          borderColor: "#1e2235",
          borderWidth: 1.5,
          pointRadius: 7,
          pointHoverRadius: 9,
          pointStyle: "triangle",
        }] : []),
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => `${items[0].parsed.x.toFixed(1)} km`,
            label: (ctx) => {
              if (ctx.dataset.label === "Aid Stations") {
                const as = aidStations[ctx.dataIndex];
                return as
                  ? `⛺ ${as.name}${as.notes ? " — " + as.notes : ""}`
                  : "Aid station";
              }
              return `${Math.round(ctx.parsed.y)} m elevation`;
            },
          },
        },
      },
      scales: {
        x: {
          type: "linear",
          ticks: { color: "#8892a4", maxTicksLimit: 12, callback: (v) => `${v}` },
          grid: { color: "#2e3348" },
          title: { display: true, text: "distance (km)", color: "#8892a4" },
        },
        y: {
          ticks: { color: "#8892a4" },
          grid: { color: "#2e3348" },
          title: { display: true, text: "elevation (m)", color: "#8892a4" },
          suggestedMin: minEle - 20,
          suggestedMax: maxEle + 20,
        },
      },
    },
  });
}

export function renderPredictionTrend(canvasId, data) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;

  const { snapshots = [], race_date } = data;
  if (snapshots.length < 2) return null;

  function fmtTimeSec(sec) {
    if (!sec || sec <= 0) return "–";
    const s = Math.round(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = s % 60;
    return h > 0
      ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`
      : `${m}:${String(ss).padStart(2, "0")}`;
  }

  function linReg(xs, ys) {
    const n = xs.length;
    const mx = xs.reduce((a, b) => a + b, 0) / n;
    const my = ys.reduce((a, b) => a + b, 0) / n;
    const num = xs.reduce((s, x, i) => s + (x - mx) * (ys[i] - my), 0);
    const den = xs.reduce((s, x) => s + (x - mx) ** 2, 0);
    const slope = den === 0 ? 0 : num / den;
    return { slope, intercept: my - slope * mx };
  }

  // ── Build projection points (dashed line, today → race day) ─────────────────
  const today = new Date().toISOString().slice(0, 10);
  const projPoints = [];   // [{date, predicted_time_sec}]

  if (race_date && race_date > today && snapshots.length >= 3) {
    // Use last 8 weeks for the regression so it reflects recent trend
    const regSnaps = snapshots.slice(-8);
    const refMs = new Date(regSnaps[0].date).getTime();
    const dayMs = 86400_000;
    const xs = regSnaps.map((s) => (new Date(s.date).getTime() - refMs) / dayMs);
    const ys = regSnaps.map((s) => s.predicted_time_sec);
    const { slope, intercept } = linReg(xs, ys);

    const raceDateObj = new Date(race_date);
    let projDate = new Date(today);
    projDate.setDate(projDate.getDate() + 7);

    while (projDate <= raceDateObj) {
      const x = (projDate.getTime() - refMs) / dayMs;
      projPoints.push({
        date: projDate.toISOString().slice(0, 10),
        predicted_time_sec: Math.max(slope * x + intercept, 1800),  // floor at 30 min
      });
      projDate.setDate(projDate.getDate() + 7);
    }

    // Ensure race day itself is included as the final projected point
    const lastDate = projPoints[projPoints.length - 1]?.date;
    if (lastDate !== race_date) {
      const x = (raceDateObj.getTime() - refMs) / dayMs;
      projPoints.push({
        date: race_date,
        predicted_time_sec: Math.max(slope * x + intercept, 1800),
      });
    }
  }

  // ── Merge historical + projected onto a single x-axis ────────────────────────
  const allPoints = [
    ...snapshots.map((s) => ({ date: s.date, historical: true, ...s })),
    ...projPoints.map((p) => ({ date: p.date, historical: false, predicted_time_sec: p.predicted_time_sec, ctl: null, run_count: null })),
  ];
  const nHist = snapshots.length;

  const labels   = allPoints.map((p) => p.date.slice(5));   // MM-DD
  const histData = allPoints.map((p, i) => (i < nHist ? p.predicted_time_sec : null));
  // Projected line starts from the last real data point so the lines connect
  const projData = allPoints.map((p, i) => (i >= nHist - 1 && !p.historical ? p.predicted_time_sec : i === nHist - 1 ? p.predicted_time_sec : null));
  const ctlData  = allPoints.map((p) => p.ctl ?? null);

  // Y-axis range across both historical and projected values
  const allTimes = [...histData, ...projData].filter(Boolean);
  const minTime = Math.min(...allTimes);
  const maxTime = Math.max(...allTimes);
  const pad = (maxTime - minTime) * 0.15 || 600;

  return new Chart(canvas, {
    data: {
      labels,
      datasets: [
        {
          type: "line",
          label: "Actual trend",
          data: histData,
          yAxisID: "y",
          borderColor: "#4f7cff",
          backgroundColor: "rgba(79,124,255,0.07)",
          fill: true,
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          tension: 0.3,
          order: 1,
        },
        {
          type: "line",
          label: "Projected to race day",
          data: projData,
          yAxisID: "y",
          borderColor: "rgba(79,124,255,0.45)",
          backgroundColor: "transparent",
          fill: false,
          borderWidth: 2,
          borderDash: [6, 4],
          pointRadius: (ctx) => ctx.dataIndex === allPoints.length - 1 ? 5 : 2,
          pointBackgroundColor: (ctx) => ctx.dataIndex === allPoints.length - 1 ? "#4f7cff" : "rgba(79,124,255,0.5)",
          tension: 0.3,
          order: 1,
        },
        {
          type: "bar",
          label: "CTL (fitness)",
          data: ctlData,
          yAxisID: "y2",
          backgroundColor: "rgba(139,148,158,0.18)",
          borderColor: "transparent",
          order: 2,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => {
              const idx = items[0].dataIndex;
              const p = allPoints[idx];
              return p.historical ? p.date : `${p.date}  (projected)`;
            },
            label: (item) => {
              const idx = item.dataIndex;
              const p = allPoints[idx];
              if (item.dataset.label === "CTL (fitness)") {
                return p.ctl != null ? `  CTL: ${p.ctl.toFixed(1)}` : null;
              }
              if (item.dataset.label === "Projected to race day" && !p.historical) {
                return `  Projected: ${fmtTimeSec(p.predicted_time_sec)}`;
              }
              if (item.dataset.label === "Actual trend" && p.historical) {
                return `  Predicted: ${fmtTimeSec(p.predicted_time_sec)}${p.run_count ? `  (${p.run_count} runs)` : ""}`;
              }
              return null;
            },
          },
          filter: (item) => item.formattedValue !== "null" && item.formattedValue !== "",
        },
      },
      scales: {
        x: {
          ticks: { color: "#8892a4", maxTicksLimit: 12 },
          grid: { color: "#2e3348" },
        },
        y: {
          position: "left",
          suggestedMin: minTime - pad,
          suggestedMax: maxTime + pad,
          ticks: {
            color: "#8892a4",
            callback: (v) => fmtTimeSec(v),
            maxTicksLimit: 6,
          },
          grid: { color: "#2e3348" },
          title: { display: true, text: "predicted finish time", color: "#8892a4", font: { size: 11 } },
        },
        y2: {
          position: "right",
          beginAtZero: true,
          ticks: { color: "#4b5563", maxTicksLimit: 5 },
          grid: { display: false },
          title: { display: true, text: "CTL", color: "#4b5563", font: { size: 11 } },
        },
      },
    },
  });
}


// ── Training analysis charts ────────────────────────────────────────────────

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

export function renderWorkoutDistribution(canvasId, distribution) {
  destroyIfExists(canvasId);
  const labels = distribution.map((d) => d.type.replace(/_/g, " "));
  const data = distribution.map((d) => d.count);
  const colors = distribution.map((d) => WORKOUT_COLORS[d.type] || "#6b7280");

  return new Chart(document.getElementById(canvasId), {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: colors,
        borderColor: "#1a1f2e",
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: {
          position: "right",
          labels: { color: "#e2e8f0", padding: 12, font: { size: 12 } },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const d = distribution[ctx.dataIndex];
              return ` ${d.count} runs (${d.pct_count}%) · ${d.total_time_min} min`;
            },
          },
        },
      },
    },
  });
}


export function renderProgressChart(canvasId, similarRuns, reference) {
  destroyIfExists(canvasId);
  if (!similarRuns || similarRuns.length === 0) return null;

  // Sort by date ascending for progression
  const sorted = [...similarRuns].sort((a, b) => a.date.localeCompare(b.date));
  const labels = sorted.map((r) => r.date.slice(5)); // MM-DD
  const paces = sorted.map((r) => r.avg_pace_sec_per_km);
  const hrs = sorted.map((r) => r.average_heartrate);

  const datasets = [
    {
      label: "Pace (sec/km)",
      data: paces,
      borderColor: "#4f7cff",
      backgroundColor: "rgba(79,124,255,0.1)",
      fill: false,
      borderWidth: 2,
      pointRadius: 4,
      tension: 0.3,
      yAxisID: "y",
    },
  ];

  if (hrs.some((h) => h != null)) {
    datasets.push({
      label: "Avg HR",
      data: hrs,
      borderColor: "#ef4444",
      backgroundColor: "rgba(239,68,68,0.1)",
      fill: false,
      borderWidth: 2,
      pointRadius: 4,
      tension: 0.3,
      yAxisID: "y2",
    });
  }

  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: "#e2e8f0", boxWidth: 16 } },
      },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 10 }, grid: { color: "#2e3348" } },
        y: {
          reverse: true,
          ticks: {
            color: "#8892a4",
            callback: (v) => {
              const m = Math.floor(v / 60), s = Math.round(v % 60);
              return `${m}:${String(s).padStart(2, "0")}`;
            },
          },
          grid: { color: "#2e3348" },
          title: { display: true, text: "Pace (min/km)", color: "#8892a4" },
        },
        y2: {
          position: "right",
          ticks: { color: "#8892a4" },
          grid: { display: false },
          title: { display: true, text: "HR (bpm)", color: "#8892a4" },
        },
      },
    },
  });
}


// ── Garmin health trend charts ─────────────────────────────────────────────

export function renderHRVTrend(canvasId, dates, hrvValues) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  return new Chart(canvas, {
    type: "line",
    data: {
      labels: dates.map((d) => d.slice(5)),
      datasets: [{
        label: "HRV (ms)",
        data: hrvValues,
        borderColor: "#22c55e",
        backgroundColor: "rgba(34,197,94,0.08)",
        fill: true,
        borderWidth: 2,
        pointRadius: 2,
        tension: 0.3,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 10 }, grid: { color: "#2e3348" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "ms", color: "#8892a4" } },
      },
    },
  });
}

export function renderRestingHRTrend(canvasId, dates, rhrValues) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  return new Chart(canvas, {
    type: "line",
    data: {
      labels: dates.map((d) => d.slice(5)),
      datasets: [{
        label: "Resting HR (bpm)",
        data: rhrValues,
        borderColor: "#ef4444",
        backgroundColor: "rgba(239,68,68,0.08)",
        fill: true,
        borderWidth: 2,
        pointRadius: 2,
        tension: 0.3,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 10 }, grid: { color: "#2e3348" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "bpm", color: "#8892a4" } },
      },
    },
  });
}

export function renderSleepTrend(canvasId, dates, sleepHours) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  return new Chart(canvas, {
    type: "bar",
    data: {
      labels: dates.map((d) => d.slice(5)),
      datasets: [{
        label: "Sleep (hrs)",
        data: sleepHours,
        backgroundColor: sleepHours.map((h) =>
          h == null ? "transparent" : h >= 7 ? "rgba(59,130,246,0.6)" : h >= 6 ? "rgba(245,158,11,0.6)" : "rgba(239,68,68,0.6)"
        ),
        borderRadius: 2,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        annotation: {
          annotations: {
            line7h: { type: "line", yMin: 7, yMax: 7, borderColor: "rgba(34,197,94,0.4)", borderWidth: 1, borderDash: [4, 4] },
            line8h: { type: "line", yMin: 8, yMax: 8, borderColor: "rgba(34,197,94,0.2)", borderWidth: 1, borderDash: [4, 4] },
          },
        },
      },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 10 }, grid: { color: "#2e3348" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "hours", color: "#8892a4" }, suggestedMin: 0, suggestedMax: 10 },
      },
    },
  });
}

export function renderRecoveryHistory(canvasId, data) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const days = data.days || [];
  const labels = days.map((d) => d.date.slice(5));
  const scores = days.map((d) => d.readiness_score);
  const tsbs = days.map((d) => d.tsb);

  return new Chart(canvas, {
    data: {
      labels,
      datasets: [
        {
          type: "line",
          label: "Readiness Score",
          data: scores,
          borderColor: "#22c55e",
          backgroundColor: (ctx) => {
            const chart = ctx.chart;
            const { ctx: c, chartArea } = chart;
            if (!chartArea) return "rgba(34,197,94,0.1)";
            const grad = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
            grad.addColorStop(0, "rgba(34,197,94,0.25)");
            grad.addColorStop(0.5, "rgba(245,158,11,0.1)");
            grad.addColorStop(1, "rgba(239,68,68,0.15)");
            return grad;
          },
          fill: true,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "TSB (Form)",
          data: tsbs,
          borderColor: "rgba(79,124,255,0.4)",
          backgroundColor: "transparent",
          fill: false,
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y2",
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 12 }, grid: { color: "#2e3348" } },
        y: { position: "left", min: 0, max: 100, ticks: { color: "#22c55e" }, grid: { color: "#2e3348" }, title: { display: true, text: "Readiness (0-100)", color: "#22c55e" } },
        y2: { position: "right", ticks: { color: "#4f7cff" }, grid: { display: false }, title: { display: true, text: "TSB", color: "#4f7cff" } },
      },
    },
  });
}

export function renderSleepEFScatter(canvasId, pairs) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas || !pairs.length) return null;
  return new Chart(canvas, {
    type: "scatter",
    data: {
      datasets: [{
        label: "Sleep vs EF",
        data: pairs.map((p) => ({ x: p.sleep_hours, y: p.ef })),
        backgroundColor: "rgba(59,130,246,0.6)",
        pointRadius: 5,
        pointHoverRadius: 7,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => { const p = pairs[ctx.dataIndex]; return p ? `${p.date} · ${p.sleep_hours}h sleep · EF ${p.ef}` : ""; } } },
      },
      scales: {
        x: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "Sleep (hours)", color: "#8892a4" } },
        y: { ticks: { color: "#8892a4" }, grid: { color: "#2e3348" }, title: { display: true, text: "Efficiency Factor", color: "#8892a4" } },
      },
    },
  });
}

export function renderHRVvdotOverlay(canvasId, data) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas || !data.length) return null;
  return new Chart(canvas, {
    data: {
      labels: data.map((d) => d.week_start.slice(5)),
      datasets: [
        { type: "line", label: "Avg HRV (ms)", data: data.map((d) => d.avg_hrv), borderColor: "#22c55e", backgroundColor: "rgba(34,197,94,0.08)", fill: true, borderWidth: 2, pointRadius: 2, tension: 0.3, yAxisID: "y", spanGaps: true },
        { type: "line", label: "Max VDOT", data: data.map((d) => d.max_vdot ?? null), borderColor: "#f59e0b", borderWidth: 2, pointRadius: 2, tension: 0.3, yAxisID: "y2", spanGaps: true },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 12 }, grid: { color: "#2e3348" } },
        y: { position: "left", ticks: { color: "#22c55e" }, grid: { color: "#2e3348" }, title: { display: true, text: "HRV (ms)", color: "#22c55e" } },
        y2: { position: "right", ticks: { color: "#f59e0b" }, grid: { display: false }, title: { display: true, text: "VDOT", color: "#f59e0b" } },
      },
    },
  });
}

export function renderRHRvsRSS(canvasId, data) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas || !data.length) return null;
  return new Chart(canvas, {
    data: {
      labels: data.map((d) => d.week_start.slice(5)),
      datasets: [
        { type: "line", label: "Resting HR", data: data.map((d) => d.avg_resting_hr), borderColor: "#ef4444", backgroundColor: "rgba(239,68,68,0.08)", fill: true, borderWidth: 2, pointRadius: 2, tension: 0.3, yAxisID: "y" },
        { type: "bar", label: "Weekly RSS", data: data.map((d) => d.total_rss), backgroundColor: "rgba(79,124,255,0.3)", borderRadius: 2, yAxisID: "y2" },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 12 }, grid: { color: "#2e3348" } },
        y: { position: "left", ticks: { color: "#ef4444" }, grid: { color: "#2e3348" }, title: { display: true, text: "RHR (bpm)", color: "#ef4444" } },
        y2: { position: "right", ticks: { color: "#4f7cff" }, grid: { display: false }, title: { display: true, text: "RSS", color: "#4f7cff" } },
      },
    },
  });
}

export function renderBatteryStressTrend(canvasId, dates, batteryValues, stressValues) {
  destroyIfExists(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  return new Chart(canvas, {
    data: {
      labels: dates.map((d) => d.slice(5)),
      datasets: [
        {
          type: "line",
          label: "Body Battery",
          data: batteryValues,
          borderColor: "#22c55e",
          backgroundColor: "rgba(34,197,94,0.08)",
          fill: true,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y",
          spanGaps: true,
        },
        {
          type: "line",
          label: "Stress",
          data: stressValues,
          borderColor: "#f59e0b",
          backgroundColor: "transparent",
          fill: false,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y2",
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#8892a4", maxTicksLimit: 10 }, grid: { color: "#2e3348" } },
        y: { position: "left", min: 0, max: 100, ticks: { color: "#22c55e" }, grid: { color: "#2e3348" }, title: { display: true, text: "Battery", color: "#22c55e" } },
        y2: { position: "right", min: 0, max: 100, ticks: { color: "#f59e0b" }, grid: { display: false }, title: { display: true, text: "Stress", color: "#f59e0b" } },
      },
    },
  });
}


const STRATEGY_COLORS = {
  even: "#4f7cff",
  negative: "#22c55e",
  conservative: "#f59e0b",
};

export function renderStrategyComparison(canvasId, strategies) {
  destroyIfExists(canvasId);
  if (!strategies || strategies.length === 0) return null;

  // Build distance axis from the first strategy's plan
  const refPlan = strategies[0].plan;
  const labels = refPlan.map((s) => (s.cum_distance_m / 1000).toFixed(1));

  const datasets = strategies.map((strat) => ({
    label: strat.label,
    data: strat.plan.map((s) => s.target_actual_pace),
    borderColor: STRATEGY_COLORS[strat.name] || "#8892a4",
    backgroundColor: "transparent",
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3,
  }));

  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#e2e8f0", boxWidth: 16 } } },
      scales: {
        x: {
          ticks: { color: "#8892a4", maxTicksLimit: 15 },
          grid: { color: "#2e3348" },
          title: { display: true, text: "Distance (km)", color: "#8892a4" },
        },
        y: {
          reverse: true,
          ticks: {
            color: "#8892a4",
            callback: (v) => {
              const m = Math.floor(v / 60), s = Math.round(v % 60);
              return `${m}:${String(s).padStart(2, "0")}`;
            },
          },
          grid: { color: "#2e3348" },
          title: { display: true, text: "Pace (min/km)", color: "#8892a4" },
        },
      },
    },
  });
}
