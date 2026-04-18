import { api } from "../api.js";
import { renderElevationProfile, renderPredictionTrend, renderStrategyComparison } from "../charts.js";
import { renderActivityMap } from "../map.js";
import { escapeHtml, fmtTime as fmtTimeSec } from "../util.js";

// ── Formatting helpers ────────────────────────────────────────────────────────

function fmtDist(m) {
  if (!m) return "–";
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

function fmtEle(m) {
  if (!m && m !== 0) return "–";
  return `${Math.round(m)} m`;
}

function gradeColor(pct) {
  const abs = Math.abs(pct);
  if (abs < 2) return "#4ade80";
  if (abs < 5) return "#facc15";
  if (abs < 8) return "#fb923c";
  return "#f87171";
}

function effortColor(pct) {
  if (pct >= 102) return "var(--green, #4ade80)";
  if (pct >= 98) return "var(--yellow, #facc15)";
  return "var(--red, #f87171)";
}

function strategyLabel(s) {
  return { even: "Even Effort", negative: "Negative Split", conservative: "Conservative" }[s] || s;
}

// ── Race list page ────────────────────────────────────────────────────────────

export async function render(container, raceId) {
  if (raceId) {
    await renderDetail(container, raceId);
  } else {
    await renderList(container);
  }
}

async function renderList(container) {
  container.innerHTML = `<div class="loading-spinner">Loading races…</div>`;

  let data;
  try {
    data = await api.races.list();
  } catch (e) {
    container.innerHTML = `<div class="loading-spinner" style="color:var(--red)">Failed to load races: ${e.message}</div>`;
    return;
  }

  const { upcoming = [], past = [], backtest = null } = data;

  container.innerHTML = `
    <div class="page-header" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
      <h1 style="margin:0">Races</h1>
      <button id="new-race-btn" class="btn-primary">+ New Race</button>
    </div>

    ${upcoming.length === 0 && past.length === 0 ? `
      <div style="text-align:center;padding:4rem 2rem;color:#8892a4">
        <div style="font-size:3rem;margin-bottom:1rem">🏁</div>
        <div style="font-size:1.1rem;margin-bottom:0.5rem">No races yet</div>
        <div style="font-size:0.9rem">Add a race and upload a GPX file to get a predicted finish time and race plan.</div>
      </div>
    ` : ""}

    ${upcoming.length > 0 ? `
      <h2 style="margin-bottom:1rem;color:#e2e8f0">Upcoming</h2>
      <div class="race-cards" style="display:grid;gap:1rem;margin-bottom:2rem">
        ${upcoming.map(raceCard).join("")}
      </div>
    ` : ""}

    ${past.length > 0 ? `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem">
        <h2 style="margin:0;color:#8892a4">Past Races</h2>
        ${backtest ? `
          <div title="Mean signed error across ${backtest.race_count} completed race(s). Positive = model predicted faster than you actually ran." style="display:flex;gap:1rem;align-items:center;font-size:0.85rem;background:#1e2235;border:1px solid #2e3348;padding:0.5rem 0.9rem;border-radius:8px">
            <span style="color:#8892a4">Predictor accuracy (${backtest.race_count} race${backtest.race_count === 1 ? "" : "s"}):</span>
            <span style="color:${Math.abs(backtest.mean_error_pct) < 2 ? "#4ade80" : Math.abs(backtest.mean_error_pct) < 5 ? "#facc15" : "#f87171"};font-weight:600">
              ${backtest.mean_error_pct >= 0 ? "+" : ""}${backtest.mean_error_pct}% mean
            </span>
            <span style="color:#8892a4">·</span>
            <span style="color:#e2e8f0">±${backtest.median_abs_error_pct}% median</span>
            ${backtest.bias_direction !== "neutral" ? `
              <span style="color:#8892a4">·</span>
              <span style="color:#8892a4;font-size:0.8rem">(${backtest.bias_direction})</span>
            ` : ""}
          </div>
        ` : ""}
      </div>
      <div class="race-cards" style="display:grid;gap:1rem">
        ${past.map((r) => raceCard(r, true)).join("")}
      </div>
    ` : ""}

    <!-- New Race Modal -->
    <div id="race-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:1000;align-items:center;justify-content:center">
      <div style="background:#1e2235;border-radius:12px;padding:2rem;width:min(480px,90vw);max-height:90vh;overflow-y:auto">
        <h2 style="margin:0 0 1.5rem">New Race</h2>
        <form id="new-race-form" enctype="multipart/form-data">
          <div style="display:grid;gap:1rem">
            <label style="display:grid;gap:4px;font-size:0.85rem;color:#8892a4">
              Race Name *
              <input name="name" required placeholder="e.g. Laugavegur Ultra" style="padding:0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.95rem">
            </label>
            <label style="display:grid;gap:4px;font-size:0.85rem;color:#8892a4">
              Race Date *
              <input name="date" type="date" required style="padding:0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.95rem">
            </label>
            <label style="display:grid;gap:4px;font-size:0.85rem;color:#8892a4">
              Location
              <input name="location" placeholder="e.g. Iceland" style="padding:0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.95rem">
            </label>
            <label style="display:grid;gap:4px;font-size:0.85rem;color:#8892a4">
              Pacing Strategy
              <select name="strategy" style="padding:0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.95rem">
                <option value="even">Even Effort (recommended)</option>
                <option value="negative">Negative Split (start easy, finish strong)</option>
                <option value="conservative">Conservative (go out very easy)</option>
              </select>
            </label>
            <label style="display:grid;gap:4px;font-size:0.85rem;color:#8892a4">
              GPX Course File <span style="color:#4f7cff">(optional but recommended)</span>
              <input name="gpx_file" type="file" accept=".gpx" style="padding:0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#8892a4;font-size:0.85rem">
            </label>
            <label style="display:grid;gap:4px;font-size:0.85rem;color:#8892a4">
              Notes
              <textarea name="notes" rows="2" placeholder="Goals, course notes, etc." style="padding:0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.95rem;resize:vertical"></textarea>
            </label>
          </div>
          <div id="form-error" style="color:#f87171;font-size:0.85rem;margin-top:0.75rem;display:none"></div>
          <div style="display:flex;gap:0.75rem;margin-top:1.5rem;justify-content:flex-end">
            <button type="button" id="cancel-modal-btn" style="padding:0.5rem 1.25rem;background:#2e3348;border:none;border-radius:6px;color:#e2e8f0;cursor:pointer">Cancel</button>
            <button type="submit" id="create-race-btn" class="btn-primary" style="padding:0.5rem 1.25rem">Create Race</button>
          </div>
        </form>
      </div>
    </div>
  `;

  // Open modal
  container.querySelector("#new-race-btn").addEventListener("click", () => {
    container.querySelector("#race-modal").style.display = "flex";
  });

  container.querySelector("#cancel-modal-btn").addEventListener("click", () => {
    container.querySelector("#race-modal").style.display = "none";
  });

  container.querySelector("#race-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) e.currentTarget.style.display = "none";
  });

  // Submit form
  container.querySelector("#new-race-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = container.querySelector("#create-race-btn");
    const errEl = container.querySelector("#form-error");
    btn.textContent = "Creating…";
    btn.disabled = true;
    errEl.style.display = "none";
    try {
      const fd = new FormData(e.target);
      const race = await api.races.create(fd);
      if (race && race.id) {
        window.navigate(`/races/${race.id}`);
      }
    } catch (err) {
      errEl.textContent = err.message;
      errEl.style.display = "block";
      btn.textContent = "Create Race";
      btn.disabled = false;
    }
  });

  // Race card click → navigate
  container.querySelectorAll(".race-card-link").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      window.navigate(el.getAttribute("href"));
    });
  });
}

function raceCard(r, isPast = false) {
  // Prefer server-computed delta + pct (keeps formatting consistent with
  // the backtest summary). Fall back to local calc for backward compat
  // if an older row is cached.
  const delta = r.prediction_delta_sec ?? (
    r.actual_time_sec && r.predicted_time_sec
      ? r.actual_time_sec - r.predicted_time_sec
      : null
  );
  const errPct = r.prediction_error_pct;
  const deltaStr = delta !== null
    ? `${delta >= 0 ? "+" : ""}${fmtTimeSec(Math.abs(delta))} vs predicted` +
      (errPct !== null && errPct !== undefined ? ` (${errPct >= 0 ? "+" : ""}${errPct}%)` : "")
    : "";

  return `
    <a href="/races/${r.id}" class="race-card-link" style="text-decoration:none">
      <div style="background:#1e2235;border-radius:10px;padding:1.25rem 1.5rem;display:grid;grid-template-columns:1fr auto;gap:0.75rem;align-items:start;border:1px solid ${isPast ? "#2e3348" : "#3b4a6b"};transition:border-color 0.15s" onmouseover="this.style.borderColor='#4f7cff'" onmouseout="this.style.borderColor='${isPast ? "#2e3348" : "#3b4a6b"}'">
        <div>
          <div style="font-size:1.05rem;font-weight:600;color:#e2e8f0;margin-bottom:0.25rem">${escapeHtml(r.name)}</div>
          <div style="font-size:0.85rem;color:#8892a4">${escapeHtml(r.date)}${r.location ? ` · ${escapeHtml(r.location)}` : ""}</div>
          <div style="display:flex;gap:1.5rem;margin-top:0.75rem;flex-wrap:wrap">
            ${r.distance_km ? `<span style="color:#e2e8f0;font-size:0.9rem">${r.distance_km} km</span>` : ""}
            ${r.total_elevation_gain ? `<span style="color:#facc15;font-size:0.9rem">↑ ${Math.round(r.total_elevation_gain)} m</span>` : ""}
            ${r.predicted_time_str ? `<span style="color:#4f7cff;font-size:0.9rem">Predicted: ${r.predicted_time_str}</span>` : ""}
            ${r.actual_time_str ? `<span style="color:#4ade80;font-size:0.9rem">Actual: ${r.actual_time_str}</span>` : ""}
          </div>
          ${deltaStr ? `<div style="font-size:0.8rem;color:${delta >= 0 ? "#f87171" : "#4ade80"};margin-top:0.25rem">${deltaStr}</div>` : ""}
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:0.4rem">
          ${r.has_gpx ? `<span style="font-size:0.75rem;background:#1a2a1a;color:#4ade80;padding:2px 8px;border-radius:99px">GPX</span>` : ""}
          <span style="font-size:0.75rem;color:#8892a4">${strategyLabel(r.plan_strategy)}</span>
        </div>
      </div>
    </a>
  `;
}

// fmtTimeSec is imported as an alias of the canonical fmtTime from util.js —
// the implementation was identical. Alias kept so we don't have to rename
// ~30 call sites in this file.

// ── Race detail page ──────────────────────────────────────────────────────────

async function renderDetail(container, raceId) {
  container.innerHTML = `<div class="loading-spinner">Loading race…</div>`;

  let race;
  try {
    race = await api.races.get(raceId);
  } catch (e) {
    container.innerHTML = `<div class="loading-spinner" style="color:var(--red)">Race not found: ${e.message}</div>`;
    return;
  }

  buildDetailUI(container, race);
}

function buildDetailUI(container, race) {
  const hasPrediction = !!race.predicted_time_sec;
  const hasActual = !!race.actual_time_sec;
  const delta = hasActual && hasPrediction ? race.actual_time_sec - race.predicted_time_sec : null;
  const bd = race.prediction_breakdown || {};

  container.innerHTML = `
    <!-- Back link + header -->
    <div style="margin-bottom:1.5rem">
      <a href="/races" id="back-link" style="color:#4f7cff;font-size:0.85rem;text-decoration:none">← All Races</a>
    </div>

    <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:1rem;margin-bottom:1.5rem">
      <div>
        <h1 style="margin:0 0 0.25rem">${escapeHtml(race.name)}</h1>
        <div style="color:#8892a4;font-size:0.9rem">
          ${escapeHtml(race.date)}${race.location ? ` · ${escapeHtml(race.location)}` : ""}
          ${race.distance_km ? ` · ${race.distance_km} km` : ""}
          ${race.total_elevation_gain ? ` · ↑ ${Math.round(race.total_elevation_gain)} m` : ""}
          ${race.total_elevation_loss ? ` ↓ ${Math.round(race.total_elevation_loss)} m` : ""}
        </div>
      </div>
      <div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:center">
        <select id="strategy-select" style="padding:0.4rem 0.75rem;background:#1e2235;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.85rem">
          <option value="even" ${race.plan_strategy === "even" ? "selected" : ""}>Even Effort</option>
          <option value="negative" ${race.plan_strategy === "negative" ? "selected" : ""}>Negative Split</option>
          <option value="conservative" ${race.plan_strategy === "conservative" ? "selected" : ""}>Conservative</option>
        </select>
        <button id="recalc-btn" style="padding:0.4rem 1rem;background:#1e2235;border:1px solid #4f7cff;border-radius:6px;color:#4f7cff;cursor:pointer;font-size:0.85rem">↻ Recalculate</button>
        ${hasPrediction && race.has_gpx ? `<button id="compare-btn" style="padding:0.4rem 1rem;background:#1e2235;border:1px solid #22c55e;border-radius:6px;color:#22c55e;cursor:pointer;font-size:0.85rem">⇔ Compare Strategies</button>` : ""}
        <button id="delete-btn" style="padding:0.4rem 0.75rem;background:transparent;border:1px solid #f87171;border-radius:6px;color:#f87171;cursor:pointer;font-size:0.85rem">Delete</button>
      </div>
    </div>

    <!-- Race Readiness (Garmin) — populated async -->
    <div id="race-readiness-panel"></div>

    <!-- Prediction card -->
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-bottom:1.5rem">
      <div style="background:#1e2235;border-radius:10px;padding:1.25rem;border:1px solid #3b4a6b">
        <div style="font-size:0.75rem;color:#8892a4;margin-bottom:0.4rem;text-transform:uppercase;letter-spacing:.05em">Predicted Time</div>
        <div style="font-size:2rem;font-weight:700;color:#4f7cff">${race.predicted_time_str || "–"}</div>
        ${race.predicted_pace_str ? `<div style="font-size:0.85rem;color:#8892a4;margin-top:0.25rem">${race.predicted_pace_str} avg</div>` : ""}
        ${bd.range_low_str && bd.range_high_str ? `
          <div style="margin-top:0.5rem">
            <div style="font-size:0.75rem;color:#8892a4;margin-bottom:0.3rem">Range: ${bd.range_low_str} – ${bd.range_high_str}</div>
            <div style="background:#2e3348;border-radius:4px;height:6px;position:relative">
              <div style="position:absolute;left:0;right:0;top:0;bottom:0;border-radius:4px;background:linear-gradient(90deg,#22c55e,#4f7cff,#f59e0b)"></div>
              ${race.predicted_time_sec && bd.range_low_sec && bd.range_high_sec ? `
                <div style="position:absolute;top:-2px;width:10px;height:10px;background:#fff;border-radius:50%;left:${Math.max(0, Math.min(100, (race.predicted_time_sec - bd.range_low_sec) / (bd.range_high_sec - bd.range_low_sec) * 100))}%"></div>
              ` : ""}
            </div>
          </div>
        ` : ""}
        <div style="margin-top:0.5rem;display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
          <span style="font-size:0.75rem;background:#1e2a40;color:#60a5fa;padding:2px 8px;border-radius:99px">${strategyLabel(race.plan_strategy)}</span>
          ${bd.data_quality ? `
            <span style="font-size:0.72rem;color:${bd.data_quality.method_count >= 3 ? '#4ade80' : bd.data_quality.method_count >= 2 ? '#facc15' : '#f87171'}">● ${bd.data_quality.method_count} method${bd.data_quality.method_count !== 1 ? 's' : ''}</span>
          ` : ""}
        </div>
        ${bd.data_quality && bd.data_quality.missing && bd.data_quality.missing.length ? `
          <div style="font-size:0.72rem;color:#facc15;margin-top:0.4rem">⚠ ${bd.data_quality.missing[0]}</div>
        ` : ""}
      </div>

      ${hasActual ? `
        <div style="background:#1e2235;border-radius:10px;padding:1.25rem;border:1px solid #2e3348">
          <div style="font-size:0.75rem;color:#8892a4;margin-bottom:0.4rem;text-transform:uppercase;letter-spacing:.05em">Actual Time</div>
          <div style="font-size:2rem;font-weight:700;color:#4ade80">${race.actual_time_str}</div>
          ${delta !== null ? `
            <div style="font-size:0.85rem;color:${delta <= 0 ? "#4ade80" : "#f87171"};margin-top:0.25rem">
              ${delta <= 0 ? "▲" : "▼"} ${fmtTimeSec(Math.abs(delta))} ${delta <= 0 ? "faster" : "slower"} than predicted
            </div>
          ` : ""}
        </div>
      ` : ""}

      ${race.total_elevation_gain ? `
        <div style="background:#1e2235;border-radius:10px;padding:1.25rem;border:1px solid #2e3348">
          <div style="font-size:0.75rem;color:#8892a4;margin-bottom:0.4rem;text-transform:uppercase;letter-spacing:.05em">Elevation</div>
          <div style="font-size:1.5rem;font-weight:700;color:#facc15">↑ ${Math.round(race.total_elevation_gain)} m</div>
          ${race.total_elevation_loss ? `<div style="font-size:0.9rem;color:#8892a4;margin-top:0.2rem">↓ ${Math.round(race.total_elevation_loss)} m</div>` : ""}
        </div>
      ` : ""}

      ${bd.sensitivity ? `
        <div style="background:#1e2235;border-radius:10px;padding:1.25rem;border:1px solid #2e3348">
          <div style="font-size:0.75rem;color:#8892a4;margin-bottom:0.4rem;text-transform:uppercase;letter-spacing:.05em">What If</div>
          <div style="font-size:0.85rem;display:grid;gap:0.25rem">
            <div><span style="color:#22c55e">Great day (−5%)</span> <span style="color:#e2e8f0;font-variant-numeric:tabular-nums">${bd.sensitivity.effort_minus_5_str}</span></div>
            <div><span style="color:#f87171">Tough day (+5%)</span> <span style="color:#e2e8f0;font-variant-numeric:tabular-nums">${bd.sensitivity.effort_plus_5_str}</span></div>
            <div><span style="color:#ef4444">Worst case (+10%)</span> <span style="color:#e2e8f0;font-variant-numeric:tabular-nums">${bd.sensitivity.effort_plus_10_str}</span></div>
          </div>
        </div>
      ` : `
        ${race.course_km_splits && race.course_km_splits.length ? `
          <div style="background:#1e2235;border-radius:10px;padding:1.25rem;border:1px solid #2e3348">
            <div style="font-size:0.75rem;color:#8892a4;margin-bottom:0.4rem;text-transform:uppercase;letter-spacing:.05em">Course</div>
            <div style="font-size:1.5rem;font-weight:700;color:#e2e8f0">${race.course_km_splits.length} seg</div>
            <div style="font-size:0.85rem;color:#8892a4;margin-top:0.2rem">${race.distance_km ? `${race.distance_km} km total` : ""}</div>
          </div>
        ` : ""}
      `}
    </div>

    <!-- Strategy comparison panel (hidden by default) -->
    <div id="strategy-panel" style="display:none;margin-bottom:1.5rem">
      <div id="strategy-loading" style="color:#8892a4;text-align:center;padding:1rem">Loading strategies…</div>
      <div id="strategy-content" style="display:none"></div>
    </div>

    <!-- Tabs -->
    <div style="display:flex;gap:0;margin-bottom:1.5rem;border-bottom:2px solid #2e3348">
      ${[
        ["plan", "Race Plan"],
        ["course", "Course"],
        ["analysis", "Analysis"],
        ["nutrition", "Nutrition"],
      ].map(([id, label]) => `
        <button class="tab-btn" data-tab="${id}" style="padding:0.6rem 1.25rem;background:transparent;border:none;cursor:pointer;font-size:0.9rem;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all 0.15s;color:#8892a4">
          ${label}
        </button>
      `).join("")}
    </div>

    <div id="tab-plan" class="tab-panel">
      ${buildPlanTab(race)}
    </div>
    <div id="tab-course" class="tab-panel" style="display:none">
      ${buildCourseTab(race)}
    </div>
    <div id="tab-analysis" class="tab-panel" style="display:none">
      ${buildAnalysisTab(race)}
    </div>
    <div id="tab-nutrition" class="tab-panel" style="display:none">
      ${buildNutritionTab(race)}
    </div>

    <div id="recalc-status" style="margin-top:1rem;font-size:0.85rem;color:#8892a4;display:none"></div>
  `;

  // Back link
  container.querySelector("#back-link").addEventListener("click", (e) => {
    e.preventDefault();
    window.navigate("/races");
  });

  // Tabs
  const tabBtns = container.querySelectorAll(".tab-btn");
  function activateTab(tabId) {
    tabBtns.forEach((b) => {
      const active = b.dataset.tab === tabId;
      b.style.color = active ? "#e2e8f0" : "#8892a4";
      b.style.borderBottomColor = active ? "#4f7cff" : "transparent";
    });
    container.querySelectorAll(".tab-panel").forEach((p) => {
      p.style.display = p.id === `tab-${tabId}` ? "block" : "none";
    });
    // Init charts when tab becomes visible (lazy, one-shot)
    if (tabId === "course") {
      if (!container._courseChartsLoaded) {
        container._courseChartsLoaded = true;
        initCourseCharts(container, race);
      }
      if (!container._courseUiInitialized) {
        container._courseUiInitialized = true;
        initCourseUI(container, race);
      }
    }
    if (tabId === "analysis" && !container._analysisChartsLoaded) {
      container._analysisChartsLoaded = true;
      initAnalysisCharts(container, race);
    }
    if (tabId === "nutrition" && !container._nutritionLoaded) {
      container._nutritionLoaded = true;
      initNutritionTab(container, race);
    }
  }
  tabBtns.forEach((b) => b.addEventListener("click", () => activateTab(b.dataset.tab)));
  activateTab("plan");

  // Race Readiness (Garmin) — async load
  (async () => {
    try {
      const readiness = await api.races.readiness(race.id);
      const panel = container.querySelector("#race-readiness-panel");
      if (!panel || !readiness?.available) return;
      const colorMap = { green: "#22c55e", yellow: "#eab308", red: "#ef4444" };
      const c = colorMap[readiness.assessment_color] || "#8892a4";
      panel.innerHTML = `
        <div style="background:rgba(${readiness.assessment_color === "green" ? "34,197,94" : readiness.assessment_color === "yellow" ? "234,179,8" : "239,68,68"},0.08);border:1px solid ${c}33;border-radius:10px;padding:1rem 1.25rem;margin-bottom:1.5rem">
          <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.75rem">
            <span style="font-size:0.75rem;color:${c};font-weight:700;text-transform:uppercase;letter-spacing:.05em">Race Readiness</span>
            <span style="background:${c}22;color:${c};font-size:0.8rem;font-weight:600;padding:2px 10px;border-radius:99px;border:1px solid ${c}44">${readiness.assessment}</span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:0.75rem">
            <div style="text-align:center"><div style="font-size:11px;color:#8892a4;margin-bottom:2px">Avg HRV</div><div style="font-size:18px;font-weight:600;color:#e2e8f0">${readiness.health_summary.avg_hrv != null ? Math.round(readiness.health_summary.avg_hrv) : "–"}<span style="font-size:11px;color:#8892a4"> ms</span></div></div>
            <div style="text-align:center"><div style="font-size:11px;color:#8892a4;margin-bottom:2px">Avg Sleep</div><div style="font-size:18px;font-weight:600;color:#e2e8f0">${readiness.health_summary.avg_sleep_hours != null ? readiness.health_summary.avg_sleep_hours.toFixed(1) : "–"}<span style="font-size:11px;color:#8892a4"> hrs</span></div></div>
            <div style="text-align:center"><div style="font-size:11px;color:#8892a4;margin-bottom:2px">Body Battery</div><div style="font-size:18px;font-weight:600;color:#e2e8f0">${readiness.health_summary.avg_body_battery != null ? Math.round(readiness.health_summary.avg_body_battery) : "–"}</div></div>
            <div style="text-align:center"><div style="font-size:11px;color:#8892a4;margin-bottom:2px">Readiness</div><div style="font-size:18px;font-weight:600;color:#e2e8f0">${readiness.health_summary.avg_training_readiness != null ? Math.round(readiness.health_summary.avg_training_readiness) : "–"}</div></div>
          </div>
          ${readiness.advice.length ? `<div style="font-size:12px;color:#8892a4;line-height:1.5">${readiness.advice.map((a) => `<div style="margin-bottom:2px">• ${a}</div>`).join("")}</div>` : ""}
        </div>
      `;
    } catch (e) { /* no Garmin data — panel stays empty */ }
  })();

  // Recalculate
  container.querySelector("#recalc-btn").addEventListener("click", async () => {
    const btn = container.querySelector("#recalc-btn");
    const status = container.querySelector("#recalc-status");
    const strategy = container.querySelector("#strategy-select").value;
    btn.textContent = "Calculating…";
    btn.disabled = true;
    status.style.display = "block";
    status.textContent = "Running prediction…";
    try {
      const updated = await api.races.predict(race.id, { strategy });
      buildDetailUI(container, updated);
    } catch (e) {
      status.textContent = `Error: ${e.message}`;
      btn.textContent = "↻ Recalculate";
      btn.disabled = false;
    }
  });

  // Compare Strategies
  const compareBtn = container.querySelector("#compare-btn");
  if (compareBtn) {
    compareBtn.addEventListener("click", async () => {
      const panel = container.querySelector("#strategy-panel");
      const loading = container.querySelector("#strategy-loading");
      const content = container.querySelector("#strategy-content");

      // Toggle panel visibility
      if (panel.style.display !== "none" && content.style.display !== "none") {
        panel.style.display = "none";
        return;
      }

      panel.style.display = "block";
      loading.style.display = "block";
      content.style.display = "none";
      compareBtn.disabled = true;
      compareBtn.textContent = "Loading…";

      try {
        const data = await api.races.strategies(race.id);
        loading.style.display = "none";
        content.style.display = "block";

        if (!data.strategies || data.strategies.length === 0) {
          content.innerHTML = `<div style="color:#8892a4;text-align:center;padding:1rem">No strategy data available.</div>`;
          return;
        }

        const stratColors = { even: "#4f7cff", negative: "#22c55e", conservative: "#f59e0b" };
        content.innerHTML = `
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-bottom:1rem">
            ${data.strategies.map((s) => `
              <div style="background:#1e2235;border-radius:10px;padding:1rem;border:2px solid ${s.name === race.plan_strategy ? stratColors[s.name] : '#2e3348'};position:relative">
                ${s.name === race.plan_strategy ? `<div style="position:absolute;top:8px;right:10px;font-size:0.65rem;background:${stratColors[s.name]};color:#000;padding:1px 6px;border-radius:4px;font-weight:600">ACTIVE</div>` : ""}
                <div style="font-size:0.8rem;color:${stratColors[s.name]};font-weight:600;margin-bottom:0.5rem">${s.label}</div>
                <div style="font-size:1.5rem;font-weight:700;color:#e2e8f0;margin-bottom:0.25rem">${s.predicted_time_str}</div>
                <div style="font-size:0.8rem;color:#8892a4;margin-bottom:0.75rem">${s.predicted_pace_str}</div>
                <div style="display:grid;gap:0.3rem;font-size:0.8rem">
                  <div style="display:flex;justify-content:space-between"><span style="color:#8892a4">1st half</span><span style="color:#e2e8f0">${s.first_half_str}</span></div>
                  <div style="display:flex;justify-content:space-between"><span style="color:#8892a4">2nd half</span><span style="color:#e2e8f0">${s.second_half_str}</span></div>
                  <div style="display:flex;justify-content:space-between"><span style="color:#8892a4">Slowest</span><span style="color:#e2e8f0">${s.slowest_km.pace_str} <span style="color:#8892a4;font-size:0.7rem">km ${s.slowest_km.km_index}</span></span></div>
                  <div style="display:flex;justify-content:space-between"><span style="color:#8892a4">Fastest</span><span style="color:#e2e8f0">${s.fastest_km.pace_str} <span style="color:#8892a4;font-size:0.7rem">km ${s.fastest_km.km_index}</span></span></div>
                </div>
                ${s.name !== race.plan_strategy ? `
                  <button data-select-strategy="${s.name}" style="margin-top:0.75rem;width:100%;padding:0.4rem;background:transparent;border:1px solid ${stratColors[s.name]};border-radius:6px;color:${stratColors[s.name]};cursor:pointer;font-size:0.8rem">Select</button>
                ` : ""}
              </div>
            `).join("")}
          </div>
          <div style="background:#1e2235;border-radius:10px;padding:1rem">
            <div style="font-size:0.8rem;color:#8892a4;margin-bottom:0.5rem">Pace Profile Comparison</div>
            <canvas id="strategy-compare-chart" height="160"></canvas>
          </div>
        `;

        renderStrategyComparison("strategy-compare-chart", data.strategies);

        // Wire up select buttons
        content.querySelectorAll("[data-select-strategy]").forEach((btn) => {
          btn.addEventListener("click", async () => {
            const strategy = btn.dataset.selectStrategy;
            btn.textContent = "Applying…";
            btn.disabled = true;
            try {
              const updated = await api.races.predict(race.id, { strategy });
              buildDetailUI(container, updated);
            } catch (err) {
              btn.textContent = `Error: ${err.message}`;
            }
          });
        });
      } catch (e) {
        loading.textContent = `Error: ${e.message}`;
      } finally {
        compareBtn.textContent = "⇔ Compare Strategies";
        compareBtn.disabled = false;
      }
    });
  }

  // Delete
  container.querySelector("#delete-btn").addEventListener("click", async () => {
    if (!confirm(`Delete "${race.name}"? This cannot be undone.`)) return;
    await api.races.delete(race.id);
    window.navigate("/races");
  });
}

function buildPlanTab(race) {
  const plan = race.race_plan || [];
  if (!plan.length) {
    return `<div style="color:#8892a4;padding:2rem 0">No race plan available. ${!race.has_gpx ? "Upload a GPX file and recalculate to generate a detailed plan." : "Click Recalculate to generate a plan."}</div>`;
  }

  // Cumulative time across variable-length segments
  let cumSec = 0;
  const rows = plan.map((seg) => {
    const segTime = seg.target_actual_pace * (seg.distance_m / 1000);
    cumSec += segTime || 0;
    const gc = gradeColor(seg.avg_grade_pct);
    const gradeStr = seg.avg_grade_pct >= 0 ? `+${seg.avg_grade_pct.toFixed(1)}%` : `${seg.avg_grade_pct.toFixed(1)}%`;
    const distKm = seg.distance_m ? (seg.distance_m / 1000).toFixed(2) : "–";
    // Use net elevation change so it's consistent with grade% = net / dist
    const net = seg.elevation_net ?? (seg.elevation_gain - seg.elevation_loss);
    const eleStr = net > 0.5
      ? `<span style="color:#facc15">↑${Math.round(net)}m</span>`
      : net < -0.5
        ? `<span style="color:#60a5fa">↓${Math.round(Math.abs(net))}m</span>`
        : `<span style="color:#4ade80">—</span>`;
    return `
      <tr style="border-bottom:1px solid #2e3348">
        <td style="padding:0.5rem 0.75rem;color:#8892a4;font-variant-numeric:tabular-nums">${seg.km_index}</td>
        <td style="padding:0.5rem 0.75rem;color:#8892a4;font-variant-numeric:tabular-nums">${distKm}</td>
        <td style="padding:0.5rem 0.75rem;color:#e2e8f0;font-weight:500;font-variant-numeric:tabular-nums">${seg.target_actual_pace_str}</td>
        <td style="padding:0.5rem 0.75rem;color:#8892a4;font-variant-numeric:tabular-nums">${seg.target_gap_pace_str}</td>
        <td style="padding:0.5rem 0.75rem;font-variant-numeric:tabular-nums"><span style="color:${gc}">${gradeStr}</span></td>
        <td style="padding:0.5rem 0.75rem">${eleStr}</td>
        <td style="padding:0.5rem 0.75rem;font-variant-numeric:tabular-nums;color:${effortColor(seg.effort_pct)}">${seg.effort_pct}%</td>
        <td style="padding:0.5rem 0.75rem;color:#8892a4;font-size:0.8rem;font-variant-numeric:tabular-nums">${fmtTimeSec(cumSec)}</td>
      </tr>
    `;
  });

  return `
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:0.9rem">
        <thead>
          <tr style="border-bottom:2px solid #2e3348;text-align:left">
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">Seg</th>
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">Dist</th>
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">Target Pace</th>
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">GAP Target</th>
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">Grade</th>
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">Elevation</th>
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">Effort</th>
            <th style="padding:0.5rem 0.75rem;color:#8892a4;font-weight:500">Split Time</th>
          </tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    </div>
    <div style="margin-top:0.75rem;font-size:0.78rem;color:#8892a4">
      Grade colours: <span style="color:#4ade80">■</span> &lt;2%  <span style="color:#facc15">■</span> 2-5%  <span style="color:#fb923c">■</span> 5-8%  <span style="color:#f87171">■</span> &gt;8%  ·
      Effort = 100% means on-target pace. &gt;100% = conservative (saving energy), &lt;100% = pushing hard.
    </div>
  `;
}

function buildCourseTab(race) {
  const hasProfile = race.elevation_profile && race.elevation_profile.length > 1;
  const hasMap = race.latlng && race.latlng.length > 1;

  if (!hasProfile && !hasMap) {
    return `<div style="color:#8892a4;padding:2rem 0">No GPS course data. Upload a GPX file to see the elevation profile and route map.</div>`;
  }

  return `
    ${hasProfile ? `
      <div style="margin-bottom:1.5rem">
        <h3 style="margin-bottom:0.75rem;color:#8892a4;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em">Elevation Profile</h3>
        <div style="background:#1e2235;border-radius:10px;padding:1rem">
          <canvas id="elevation-chart" height="150"></canvas>
        </div>
      </div>
    ` : ""}
    <!-- Aid station management (before the map so it's immediately visible) -->
    <div style="margin-bottom:1.5rem">
      <h3 style="margin-bottom:0.75rem;color:#8892a4;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em">Aid Stations</h3>
      <div style="background:#1e2235;border-radius:10px;padding:1rem">
        <div id="aid-station-list">${buildAidStationListHtml(race.aid_stations || [])}</div>
        <div style="display:flex;gap:0.5rem;margin-top:0.75rem;flex-wrap:wrap;align-items:center">
          <input id="as-name" placeholder="Station name" style="flex:1;min-width:120px;padding:0.4rem 0.6rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.85rem">
          <input id="as-dist" type="number" step="0.1" min="0.1" placeholder="km" style="width:80px;padding:0.4rem 0.6rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.85rem">
          <input id="as-notes" placeholder="Notes (optional)" style="flex:1;min-width:120px;padding:0.4rem 0.6rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.85rem">
          <label style="display:inline-flex;align-items:center;gap:0.25rem;color:#8892a4;font-size:0.8rem;cursor:pointer;user-select:none"><input id="as-water" type="checkbox" checked>💧 Water</label>
          <label style="display:inline-flex;align-items:center;gap:0.25rem;color:#8892a4;font-size:0.8rem;cursor:pointer;user-select:none"><input id="as-food"  type="checkbox">🍌 Food</label>
          <label style="display:inline-flex;align-items:center;gap:0.25rem;color:#8892a4;font-size:0.8rem;cursor:pointer;user-select:none"><input id="as-bags"  type="checkbox">🎒 Bags</label>
          <button id="as-add-btn" class="btn-primary" style="padding:0.4rem 0.9rem;font-size:0.85rem;white-space:nowrap">Add</button>
        </div>
        <div id="as-error" style="display:none;color:#f87171;font-size:0.8rem;margin-top:0.4rem"></div>
      </div>
    </div>

    ${hasMap ? `
      <div>
        <h3 style="margin-bottom:0.75rem;color:#8892a4;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em">Course Map</h3>
        <div id="race-map" style="height:400px;border-radius:10px;overflow:hidden"></div>
      </div>
    ` : ""}
  `;
}

function initCourseCharts(container, race) {
  const stations = race.aid_stations || [];
  if (race.elevation_profile && race.elevation_profile.length > 1) {
    renderElevationProfile("elevation-chart", race.elevation_profile, stations);
  }
  if (race.latlng && race.latlng.length > 1) {
    renderActivityMap("race-map", race.latlng, null, "pace", {
      aidStations: stations,
      totalDistanceM: race.distance_m || 0,
      latlngCumDist: race.latlng_dist || [],
    });
  }
}

// ── Aid station helpers ───────────────────────────────────────────────────────

function buildAidStationListHtml(stations, editIdx = -1) {
  if (!stations.length) {
    return `<div style="color:#4b5563;font-size:0.85rem;padding:0.5rem 0">No aid stations added yet.</div>`;
  }
  const inputStyle = `padding:0.3rem 0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:5px;color:#e2e8f0;font-size:0.82rem`;
  const chkLabelStyle = `display:inline-flex;align-items:center;gap:0.25rem;color:#8892a4;font-size:0.8rem;cursor:pointer;user-select:none`;
  // Available-provisions pills shown in the read-only row, dim when absent
  const chipHtml = (on, icon, tip) =>
    `<span style="font-size:0.9rem;opacity:${on ? "1" : "0.18"};filter:${on ? "none" : "grayscale(1)"}" title="${tip}: ${on ? "yes" : "no"}">${icon}</span>`;

  return stations.map((as, i) => {
    // Water defaults to true (most aid stations have it). Missing flags
    // on older rows before this feature land are treated as water-yes /
    // food-no / bags-no.
    const hasWater = as.has_water !== false;
    const hasFood = !!as.has_food;
    const hasBags = !!as.has_bags;

    if (i === editIdx) {
      // ── Inline edit row ──
      return `
        <div style="display:flex;align-items:center;gap:0.5rem;padding:0.45rem 0;border-bottom:1px solid #2e3348;flex-wrap:wrap">
          <span style="color:#facc15;font-size:1rem">⛺</span>
          <input data-edit-name value="${escapeHtml(as.name)}" placeholder="Name" style="${inputStyle};flex:1;min-width:100px">
          <input data-edit-dist type="number" value="${as.distance_km}" step="0.1" min="0.1" placeholder="km" style="${inputStyle};width:70px">
          <input data-edit-notes value="${escapeHtml(as.notes || '')}" placeholder="Notes" style="${inputStyle};flex:1;min-width:80px">
          <input data-edit-lat type="number" value="${as.lat ?? ''}" step="0.0001" placeholder="Lat" title="Latitude (optional — for map pin)" style="${inputStyle};width:75px">
          <input data-edit-lon type="number" value="${as.lon ?? ''}" step="0.0001" placeholder="Lon" title="Longitude (optional — for map pin)" style="${inputStyle};width:75px">
          <label style="${chkLabelStyle}"><input data-edit-water type="checkbox" ${hasWater ? "checked" : ""}>💧 Water</label>
          <label style="${chkLabelStyle}"><input data-edit-food  type="checkbox" ${hasFood  ? "checked" : ""}>🍌 Food</label>
          <label style="${chkLabelStyle}"><input data-edit-bags  type="checkbox" ${hasBags  ? "checked" : ""}>🎒 Bags</label>
          <button data-save-as="${i}" style="padding:0.3rem 0.7rem;background:#4f7cff;border:none;border-radius:5px;color:#fff;cursor:pointer;font-size:0.82rem">✓ Save</button>
          <button data-cancel-as="${i}" style="background:none;border:none;color:#8892a4;cursor:pointer;font-size:0.9rem;padding:0 0.2rem" title="Cancel">✕</button>
        </div>`;
    }
    // ── Normal row ──
    return `
      <div style="display:flex;align-items:center;gap:0.75rem;padding:0.4rem 0;border-bottom:1px solid #2e3348">
        <span style="color:#facc15;font-size:1rem">⛺</span>
        <span style="flex:1;color:#e2e8f0;font-size:0.9rem">${escapeHtml(as.name)}</span>
        <span style="color:#8892a4;font-size:0.85rem;font-variant-numeric:tabular-nums">${as.distance_km} km</span>
        <span style="display:inline-flex;gap:0.25rem;align-items:center">
          ${chipHtml(hasWater, "💧", "Water")}
          ${chipHtml(hasFood, "🍌", "Food")}
          ${chipHtml(hasBags, "🎒", "Drop bag")}
        </span>
        ${as.notes ? `<span style="color:#4b5563;font-size:0.8rem;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(as.notes)}">${escapeHtml(as.notes)}</span>` : ""}
        <button data-edit-as="${i}" style="background:none;border:none;color:#4b5563;cursor:pointer;font-size:0.85rem;padding:0 0.2rem;line-height:1" title="Edit">✎</button>
        <button data-delete-as="${i}" style="background:none;border:none;color:#4b5563;cursor:pointer;font-size:1rem;padding:0 0.2rem;line-height:1" title="Remove">✕</button>
      </div>`;
  }).join("");
}

function initCourseUI(container, race) {
  let editingIdx = -1;

  function refreshList() {
    const listEl = container.querySelector("#aid-station-list");
    if (listEl) listEl.innerHTML = buildAidStationListHtml(race.aid_stations || [], editingIdx);
  }

  function refreshNutrition() {
    const nutritionBody = container.querySelector("#nutrition-plan-body");
    if (nutritionBody) {
      const s = race.nutrition_settings || { sweat_rate_ml_per_hr: 500, cal_per_hr: 250, carry_capacity_ml: 1500 };
      nutritionBody.innerHTML = nutritionPlanHtml(computeNutritionPlan(race, s), s);
    }
  }

  async function saveStations(newStations) {
    const errEl = container.querySelector("#as-error");
    try {
      const updated = await api.races.setAidStations(race.id, newStations);
      race.aid_stations = updated.aid_stations;
      editingIdx = -1;
      refreshList();
      initCourseCharts(container, race);
      refreshNutrition();
      if (errEl) errEl.style.display = "none";
    } catch (e) {
      if (errEl) { errEl.textContent = `Error: ${e.message}`; errEl.style.display = "block"; }
    }
  }

  function validateStation(name, dist, errEl) {
    if (!name) { if (errEl) { errEl.textContent = "Station name is required."; errEl.style.display = "block"; } return false; }
    if (!dist || dist <= 0) { if (errEl) { errEl.textContent = "Enter a valid distance > 0."; errEl.style.display = "block"; } return false; }
    const maxKm = race.distance_m ? race.distance_m / 1000 : Infinity;
    if (dist > maxKm) { if (errEl) { errEl.textContent = `Distance must be ≤ ${maxKm.toFixed(1)} km.`; errEl.style.display = "block"; } return false; }
    return true;
  }

  // Add button
  const addBtn = container.querySelector("#as-add-btn");
  if (addBtn) {
    addBtn.addEventListener("click", async () => {
      const nameEl = container.querySelector("#as-name");
      const distEl = container.querySelector("#as-dist");
      const notesEl = container.querySelector("#as-notes");
      const errEl = container.querySelector("#as-error");
      const name = nameEl.value.trim();
      const dist = parseFloat(distEl.value);
      const notes = notesEl ? notesEl.value.trim() : "";
      const hasWater = container.querySelector("#as-water")?.checked ?? true;
      const hasFood = container.querySelector("#as-food")?.checked ?? false;
      const hasBags = container.querySelector("#as-bags")?.checked ?? false;
      if (!validateStation(name, dist, errEl)) return;
      const newStations = [
        ...(race.aid_stations || []),
        { name, distance_km: dist, notes, has_water: hasWater, has_food: hasFood, has_bags: hasBags },
      ];
      addBtn.disabled = true;
      await saveStations(newStations);
      nameEl.value = ""; distEl.value = ""; if (notesEl) notesEl.value = "";
      // Reset add-form checkboxes to their defaults (water yes, food/bags no)
      const waterEl = container.querySelector("#as-water");
      const foodEl = container.querySelector("#as-food");
      const bagsEl = container.querySelector("#as-bags");
      if (waterEl) waterEl.checked = true;
      if (foodEl) foodEl.checked = false;
      if (bagsEl) bagsEl.checked = false;
      addBtn.disabled = false;
    });
  }

  // Edit / delete / save-edit / cancel-edit (event delegation on the list)
  const listEl = container.querySelector("#aid-station-list");
  if (listEl) {
    listEl.addEventListener("click", async (e) => {
      const errEl = container.querySelector("#as-error");

      // ── Enter edit mode ──
      const editBtn = e.target.closest("[data-edit-as]");
      if (editBtn) {
        editingIdx = parseInt(editBtn.dataset.editAs);
        refreshList();
        // Focus name input in the newly rendered row
        const nameInput = listEl.querySelector("[data-edit-name]");
        if (nameInput) nameInput.focus();
        return;
      }

      // ── Cancel edit ──
      const cancelBtn = e.target.closest("[data-cancel-as]");
      if (cancelBtn) {
        editingIdx = -1;
        refreshList();
        return;
      }

      // ── Save edit ──
      const saveBtn = e.target.closest("[data-save-as]");
      if (saveBtn) {
        const idx = parseInt(saveBtn.dataset.saveAs);
        const row = saveBtn.closest("div");
        const name = row.querySelector("[data-edit-name]").value.trim();
        const dist = parseFloat(row.querySelector("[data-edit-dist]").value);
        const notes = row.querySelector("[data-edit-notes]").value.trim();
        const latVal = row.querySelector("[data-edit-lat]").value.trim();
        const lonVal = row.querySelector("[data-edit-lon]").value.trim();
        if (!validateStation(name, dist, errEl)) return;
        const updated = {
          name,
          distance_km: dist,
          notes,
          has_water: row.querySelector("[data-edit-water]").checked,
          has_food: row.querySelector("[data-edit-food]").checked,
          has_bags: row.querySelector("[data-edit-bags]").checked,
        };
        if (latVal) updated.lat = parseFloat(latVal);
        if (lonVal) updated.lon = parseFloat(lonVal);
        const newStations = (race.aid_stations || []).map((s, i) =>
          i === idx ? updated : s
        );
        saveBtn.disabled = true;
        await saveStations(newStations);
        return;
      }

      // ── Delete ──
      const deleteBtn = e.target.closest("[data-delete-as]");
      if (deleteBtn && editingIdx === -1) {
        const idx = parseInt(deleteBtn.dataset.deleteAs);
        const newStations = (race.aid_stations || []).filter((_, i) => i !== idx);
        await saveStations(newStations);
      }
    });
  }
}

async function initAnalysisCharts(container, race) {
  const loading = container.querySelector("#prediction-trend-loading");
  const canvas = container.querySelector("#prediction-trend-chart");
  const summary = container.querySelector("#prediction-trend-summary");

  try {
    const data = await api.races.predictionHistory(race.id);
    const snaps = data.snapshots || [];

    if (snaps.length < 2) {
      if (loading) loading.textContent = "Keep training — the trend will appear here once you have more weekly data.";
      return;
    }

    if (loading) loading.style.display = "none";
    if (canvas) canvas.style.display = "block";

    renderPredictionTrend("prediction-trend-chart", data);

    if (summary) {
      const latestCtl = snaps[snaps.length - 1]?.ctl;
      const today = new Date().toISOString().slice(0, 10);
      const hasProjection = data.race_date && data.race_date > today && snaps.length >= 3;
      summary.textContent = `${snaps.length} weekly snapshots · CTL ${latestCtl != null ? latestCtl.toFixed(1) : "–"} · solid = actual trend · dashed = projected to race day${hasProjection ? "" : " (race already past)"}`;
    }
  } catch (e) {
    if (loading) loading.textContent = "Could not load prediction trend.";
  }
}

function buildAnalysisTab(race) {
  const bd = race.prediction_breakdown || {};
  const methods = Array.isArray(bd.breakdown) ? bd.breakdown : [];

  if (!methods.length && !bd.error) {
    return `<div style="color:#8892a4;padding:2rem 0">No prediction analysis available. Add training data and recalculate.</div>`;
  }

  if (bd.error) {
    return `<div style="color:#f87171;padding:2rem 0">${bd.error}</div>`;
  }

  const methodLabel = {
    recent_race: "Recent Race",
    riegel: "Riegel Scaling",
    simulation: "Course Simulation",
    vdot: "VDOT Estimate",
    watch_vo2max: "Garmin VO2max",
    lt_pace: "Lactate Threshold Pace",
  };
  const methodCards = methods.map((m) => {
    const confColor = { high: "#4ade80", medium: "#facc15", low: "#f87171" }[m.confidence] || "#8892a4";
    const label = methodLabel[m.method] || m.method || "Prediction";
    return `
      <div style="background:#161b2e;border-radius:8px;padding:1rem;border-left:3px solid ${confColor}">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:0.4rem">
          <span style="font-weight:500;color:#e2e8f0">${label}</span>
          <span style="font-size:1.1rem;color:#4f7cff;font-variant-numeric:tabular-nums">${m.time_str || "–"}</span>
        </div>
        <div style="font-size:0.8rem;color:#8892a4">${m.source || ""}</div>
        <div style="font-size:0.78rem;margin-top:0.25rem">
          <span style="color:${confColor}">● ${m.confidence} confidence</span>
          ${m.pace_str ? `<span style="color:#8892a4;margin-left:0.75rem">${m.pace_str}</span>` : ""}
        </div>
      </div>
    `;
  });

  const tsbPct = bd.tsb_adjustment_pct;
  const tsbColor = tsbPct < 0 ? "#4ade80" : tsbPct > 0 ? "#f87171" : "#8892a4";

  return `
    <div style="display:grid;gap:1rem;max-width:600px">

      <!-- Prediction trend chart (populated async when tab activates) -->
      <div>
        <h3 style="margin:0 0 0.75rem;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em;color:#8892a4">Predicted Finish Time Trend</h3>
        <div style="background:#1e2235;border-radius:10px;padding:1rem">
          <div id="prediction-trend-loading" style="color:#8892a4;text-align:center;padding:1.5rem 0;font-size:0.9rem">Loading trend data…</div>
          <canvas id="prediction-trend-chart" height="200" style="display:none"></canvas>
          <div id="prediction-trend-summary" style="font-size:0.75rem;color:#4b5563;margin-top:0.4rem;text-align:right"></div>
        </div>
      </div>

      <div>
        <h3 style="margin:0 0 0.75rem;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em;color:#8892a4">Prediction Methods</h3>
        <div style="display:grid;gap:0.75rem">${methodCards.join("")}</div>
      </div>

      ${bd.tsb !== null && bd.tsb !== undefined ? `
        <div style="background:#1e2235;border-radius:8px;padding:1rem;margin-top:0.5rem">
          <div style="font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em;color:#8892a4;margin-bottom:0.5rem">Form Adjustment (TSB)</div>
          <div style="display:flex;gap:2rem;align-items:baseline">
            <div>
              <span style="font-size:1.3rem;font-weight:600;color:${bd.tsb > 5 ? "#4ade80" : bd.tsb < -5 ? "#f87171" : "#e2e8f0"}">${bd.tsb.toFixed(1)}</span>
              <span style="font-size:0.8rem;color:#8892a4;margin-left:4px">TSB</span>
            </div>
            <div style="color:${tsbColor};font-size:0.9rem">
              ${tsbPct >= 0 ? "+" : ""}${tsbPct?.toFixed(1)}% to predicted time
            </div>
          </div>
          <div style="font-size:0.78rem;color:#8892a4;margin-top:0.5rem">
            TSB &gt; 10 = fresh (faster) · TSB &lt; 0 = fatigued (slower)
          </div>
        </div>
      ` : ""}

      ${bd.sensitivity ? `
        <div>
          <h3 style="margin:0 0 0.75rem;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em;color:#8892a4">Sensitivity Analysis — What If</h3>
          <div style="background:#161b2e;border-radius:8px;overflow:hidden">
            <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
              <thead>
                <tr style="border-bottom:1px solid #2e3348">
                  <th style="text-align:left;padding:0.6rem 1rem;color:#8892a4;font-weight:500">Scenario</th>
                  <th style="text-align:right;padding:0.6rem 1rem;color:#8892a4;font-weight:500">Finish Time</th>
                </tr>
              </thead>
              <tbody>
                <tr style="border-bottom:1px solid #1e2235">
                  <td style="padding:0.5rem 1rem;color:#22c55e">Great day (−10%)</td>
                  <td style="padding:0.5rem 1rem;text-align:right;color:#e2e8f0;font-variant-numeric:tabular-nums">${bd.sensitivity.effort_minus_10_str}</td>
                </tr>
                <tr style="border-bottom:1px solid #1e2235">
                  <td style="padding:0.5rem 1rem;color:#4ade80">Good day (−5%)</td>
                  <td style="padding:0.5rem 1rem;text-align:right;color:#e2e8f0;font-variant-numeric:tabular-nums">${bd.sensitivity.effort_minus_5_str}</td>
                </tr>
                <tr style="border-bottom:1px solid #1e2235;background:#1e2a40">
                  <td style="padding:0.5rem 1rem;color:#4f7cff;font-weight:600">Predicted</td>
                  <td style="padding:0.5rem 1rem;text-align:right;color:#4f7cff;font-weight:600;font-variant-numeric:tabular-nums">${race.predicted_time_str || "–"}</td>
                </tr>
                <tr style="border-bottom:1px solid #1e2235">
                  <td style="padding:0.5rem 1rem;color:#f59e0b">Tough day (+5%)</td>
                  <td style="padding:0.5rem 1rem;text-align:right;color:#e2e8f0;font-variant-numeric:tabular-nums">${bd.sensitivity.effort_plus_5_str}</td>
                </tr>
                <tr>
                  <td style="padding:0.5rem 1rem;color:#ef4444">Worst case (+10%)</td>
                  <td style="padding:0.5rem 1rem;text-align:right;color:#e2e8f0;font-variant-numeric:tabular-nums">${bd.sensitivity.effort_plus_10_str}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      ` : ""}

      ${bd.data_quality ? `
        <div>
          <h3 style="margin:0 0 0.75rem;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em;color:#8892a4">Data Quality</h3>
          <div style="background:#161b2e;border-radius:8px;padding:1rem;display:grid;gap:0.5rem">
            <div style="font-size:0.85rem;display:flex;align-items:center;gap:0.5rem">
              <span style="color:${bd.data_quality.has_pr ? '#4ade80' : '#f87171'}">${bd.data_quality.has_pr ? '✓' : '✗'}</span>
              <span style="color:#e2e8f0">Riegel Scaling</span>
              <span style="color:#8892a4;font-size:0.78rem">${bd.data_quality.has_pr ? "— PR-based distance scaling" : "— no race PRs found"}</span>
            </div>
            <div style="font-size:0.85rem;display:flex;align-items:center;gap:0.5rem">
              <span style="color:${bd.data_quality.has_simulation ? '#4ade80' : '#f87171'}">${bd.data_quality.has_simulation ? '✓' : '✗'}</span>
              <span style="color:#e2e8f0">Course Simulation</span>
              <span style="color:#8892a4;font-size:0.78rem">${bd.data_quality.has_simulation ? `— ${bd.data_quality.training_runs} recent runs` : "— no recent training data"}</span>
            </div>
            <div style="font-size:0.85rem;display:flex;align-items:center;gap:0.5rem">
              <span style="color:${bd.data_quality.has_vdot ? '#4ade80' : '#f87171'}">${bd.data_quality.has_vdot ? '✓' : '✗'}</span>
              <span style="color:#e2e8f0">VDOT Estimate</span>
              <span style="color:#8892a4;font-size:0.78rem">${bd.data_quality.has_vdot ? "— VO2max-based prediction" : "— no VO2max estimate"}</span>
            </div>
            ${bd.data_quality.missing.length ? `
              <div style="margin-top:0.5rem;padding-top:0.5rem;border-top:1px solid #2e3348">
                ${bd.data_quality.missing.map((m) => `
                  <div style="font-size:0.78rem;color:#facc15;margin-top:0.25rem">⚠ ${m}</div>
                `).join("")}
              </div>
            ` : `
              <div style="margin-top:0.5rem;padding-top:0.5rem;border-top:1px solid #2e3348;font-size:0.78rem;color:#4ade80">
                ✓ All prediction methods active — high confidence
              </div>
            `}
          </div>
        </div>
      ` : `
        <div style="font-size:0.8rem;color:#8892a4;padding:0.75rem;background:#161b2e;border-radius:8px">
          <div style="margin-bottom:0.4rem;font-weight:500;color:#e2e8f0">How predictions are calculated</div>
          <div><strong style="color:#e2e8f0">Riegel Scaling</strong> — scales from your best similar-distance PR using T₂ = T₁ × (D₂/D₁)¹·⁰⁶</div>
          <div style="margin-top:0.25rem"><strong style="color:#e2e8f0">Course Simulation</strong> — uses your recent aerobic GAP pace with per-km grade adjustments (Minetti energy cost model)</div>
          <div style="margin-top:0.25rem"><strong style="color:#e2e8f0">VDOT Estimate</strong> — uses Jack Daniels VDOT from your training data, scaled to race distance</div>
          <div style="margin-top:0.25rem"><strong style="color:#e2e8f0">Form Adjustment</strong> — TSB (Training Stress Balance) from your recent training load</div>
        </div>
      `}
    </div>
  `;
}

// ── Nutrition planner ─────────────────────────────────────────────────────────

function buildNutritionTab(race) {
  const s = race.nutrition_settings || { sweat_rate_ml_per_hr: 500, cal_per_hr: 250, carry_capacity_ml: 1500 };
  // Render the plan table immediately — all data is synchronously available
  const result = computeNutritionPlan(race, s);
  return `
    <div style="margin-bottom:1.5rem">
      <h3 style="margin:0 0 0.75rem;font-size:0.85rem;text-transform:uppercase;letter-spacing:.05em;color:#8892a4">Settings</h3>
      <div style="background:#1e2235;border-radius:10px;padding:1rem;display:flex;gap:1rem;flex-wrap:wrap;align-items:flex-end">
        <label style="display:flex;flex-direction:column;gap:0.3rem;font-size:0.8rem;color:#8892a4">
          Sweat rate (ml/hr)
          <input id="nutr-sweat" type="number" value="${s.sweat_rate_ml_per_hr}" min="100" max="2000" step="50"
            style="width:110px;padding:0.4rem 0.6rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.9rem">
        </label>
        <label style="display:flex;flex-direction:column;gap:0.3rem;font-size:0.8rem;color:#8892a4">
          Calories (/hr)
          <input id="nutr-cal" type="number" value="${s.cal_per_hr}" min="50" max="600" step="25"
            style="width:100px;padding:0.4rem 0.6rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.9rem">
        </label>
        <label style="display:flex;flex-direction:column;gap:0.3rem;font-size:0.8rem;color:#8892a4">
          Carry capacity (ml)
          <input id="nutr-cap" type="number" value="${s.carry_capacity_ml}" min="250" max="5000" step="100"
            style="width:120px;padding:0.4rem 0.6rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.9rem">
        </label>
        <div style="display:flex;align-items:center;gap:0.6rem;padding-bottom:2px">
          <button id="nutr-save-btn" class="btn-primary" style="padding:0.4rem 0.9rem;font-size:0.85rem">Save</button>
          <span id="nutr-save-status" style="display:none;color:#4ade80;font-size:0.8rem">Saved ✓</span>
        </div>
      </div>
      <div style="font-size:0.75rem;color:#4b5563;margin-top:0.4rem;padding-left:0.25rem">
        Adjust to your conditions — hot weather or hard effort typically means higher sweat rate.
      </div>
    </div>
    <div id="nutrition-plan-body">${nutritionPlanHtml(result, s)}</div>
  `;
}

/**
 * Pure computation: build per-leg hydration/calorie plan from race data + settings.
 * Returns { rows, total_water_ml, total_cal, has_stations } or null if no race plan.
 */
function computeNutritionPlan(race, settings) {
  const plan = race.race_plan || [];
  if (!plan.length) return null;

  const { sweat_rate_ml_per_hr = 500, cal_per_hr = 250, carry_capacity_ml = 1500 } = settings;

  // Build time-distance curve
  let cumSec = 0;
  const timeCurve = [{ dist_m: 0, cum_sec: 0 }];
  for (const seg of plan) {
    cumSec += seg.target_actual_pace * (seg.distance_m / 1000);
    timeCurve.push({ dist_m: seg.cum_distance_m, cum_sec: cumSec });
  }
  const totalDist_m = timeCurve[timeCurve.length - 1].dist_m;

  function timeAtDist(distM) {
    if (distM <= 0) return 0;
    if (distM >= totalDist_m) return cumSec;
    for (let i = 1; i < timeCurve.length; i++) {
      if (timeCurve[i].dist_m >= distM) {
        const t0 = timeCurve[i - 1], t1 = timeCurve[i];
        const span = t1.dist_m - t0.dist_m;
        const frac = span > 0 ? (distM - t0.dist_m) / span : 0;
        return t0.cum_sec + frac * (t1.cum_sec - t0.cum_sec);
      }
    }
    return cumSec;
  }

  // Checkpoints: start + sorted stations + finish. Start has water+food
  // implicitly (you begin fully stocked); finish doesn't matter.
  const stations = [...(race.aid_stations || [])].sort((a, b) => a.distance_km - b.distance_km);
  const checkpoints = [
    { name: "Start", dist_m: 0, isStart: true, hasWater: true, hasFood: true, hasBags: false },
    ...stations.map(s => ({
      name: s.name,
      dist_m: s.distance_km * 1000,
      isStation: true,
      dist_km: s.distance_km,
      // Default water to true when flag is unset (legacy rows), food/bags to false.
      hasWater: s.has_water !== false,
      hasFood: !!s.has_food,
      hasBags: !!s.has_bags,
    })),
    { name: "Finish", dist_m: totalDist_m, isFinish: true, hasWater: true, hasFood: true, hasBags: false },
  ];

  // Look forward to find the next checkpoint with water / with food. If no
  // such station exists before the finish, you need to carry enough for
  // the whole remainder.
  function nextWithWaterAfter(idx) {
    for (let j = idx + 1; j < checkpoints.length; j++) {
      if (checkpoints[j].hasWater) return checkpoints[j];
    }
    return checkpoints[checkpoints.length - 1];
  }
  function nextWithFoodAfter(idx) {
    for (let j = idx + 1; j < checkpoints.length; j++) {
      if (checkpoints[j].hasFood) return checkpoints[j];
    }
    return checkpoints[checkpoints.length - 1];
  }

  let totalWater = 0, totalCal = 0;
  const rows = checkpoints.map((cp, i) => {
    const arriveSec = timeAtDist(cp.dist_m);
    const prevSec = i > 0 ? timeAtDist(checkpoints[i - 1].dist_m) : 0;
    const legSec = arriveSec - prevSec;
    const legHr = legSec / 3600;
    const waterInLeg = Math.round(legHr * sweat_rate_ml_per_hr);
    const calInLeg = Math.round(legHr * cal_per_hr);

    if (i > 0) { totalWater += waterInLeg; totalCal += calInLeg; }

    // Carry-out: how much to load up before leaving this checkpoint. Skip
    // stations without water — nothing to fill from — and extend the
    // coverage window to the NEXT station that actually has water.
    let carryOutWater = null, carryOutCal = null, overCapacity = false;
    if (!cp.isFinish) {
      const nextWater = cp.hasWater ? nextWithWaterAfter(i) : null;
      const nextFood = cp.hasFood ? nextWithFoodAfter(i) : null;

      if (cp.hasWater) {
        const coverHr = (timeAtDist(nextWater.dist_m) - arriveSec) / 3600;
        const needed = coverHr * sweat_rate_ml_per_hr;
        overCapacity = needed > carry_capacity_ml;
        carryOutWater = Math.min(Math.round(needed * 1.2), carry_capacity_ml);
      }
      if (cp.hasFood) {
        const coverHr = (timeAtDist(nextFood.dist_m) - arriveSec) / 3600;
        carryOutCal = Math.round(coverHr * cal_per_hr);
      }
    }

    return {
      name: cp.name,
      distKm: cp.dist_km || (cp.dist_m / 1000),
      isStart: !!cp.isStart,
      isFinish: !!cp.isFinish,
      hasWater: cp.hasWater,
      hasFood: cp.hasFood,
      hasBags: cp.hasBags,
      arriveSec,
      legSec: i > 0 ? legSec : null,
      waterInLeg: i > 0 ? waterInLeg : null,
      calInLeg: i > 0 ? calInLeg : null,
      carryOutWater,
      carryOutCal,
      overCapacity,
    };
  });

  return { rows, total_water_ml: totalWater, total_cal: totalCal, has_stations: stations.length > 0 };
}

/** Returns HTML string for the nutrition plan body — pure, no DOM side-effects. */
function nutritionPlanHtml(result, settings) {
  if (!result) {
    return `<div style="color:#8892a4;padding:2rem 0">No race plan available — add a GPX file and click Recalculate first.</div>`;
  }

  const fmtSec = (s) => {
    if (!s && s !== 0) return "–";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.round(s % 60);
    return h > 0 ? `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}` : `${m}:${String(sec).padStart(2,"0")}`;
  };
  const fmtLegTime = (s) => {
    if (!s) return "–";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  };
  const fmtWater = (ml) => ml >= 1000 ? `${(ml / 1000).toFixed(1)} L` : `${ml} ml`;

  // Summary banner (always shown)
  const summaryHtml = `
    <div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin-bottom:1.5rem">
      <div style="background:#1e2235;border-radius:10px;padding:0.9rem 1.25rem;flex:1;min-width:130px">
        <div style="font-size:0.75rem;color:#8892a4;text-transform:uppercase;letter-spacing:.05em;margin-bottom:0.3rem">💧 Total Water</div>
        <div style="font-size:1.4rem;font-weight:600;color:#60a5fa">${fmtWater(result.total_water_ml)}</div>
      </div>
      <div style="background:#1e2235;border-radius:10px;padding:0.9rem 1.25rem;flex:1;min-width:130px">
        <div style="font-size:0.75rem;color:#8892a4;text-transform:uppercase;letter-spacing:.05em;margin-bottom:0.3rem">🔥 Total Calories</div>
        <div style="font-size:1.4rem;font-weight:600;color:#fb923c">${result.total_cal.toLocaleString()} kcal</div>
      </div>
    </div>
  `;

  if (!result.has_stations) {
    return summaryHtml + `
      <div style="background:#1e2235;border-radius:10px;padding:1rem;color:#8892a4;font-size:0.85rem">
        Add aid stations in the <strong style="color:#e2e8f0">Course</strong> tab to see per-station carry-out recommendations.
      </div>`;
  }

  const thStyle = `padding:0.5rem 0.75rem;text-align:left;font-size:0.75rem;color:#8892a4;font-weight:500;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap`;
  const tdStyle = `padding:0.6rem 0.75rem;font-size:0.85rem;color:#e2e8f0;vertical-align:middle`;
  const tdSubStyle = `padding:0.6rem 0.75rem;font-size:0.85rem;color:#8892a4;vertical-align:middle`;

  // Small dim-when-absent availability icons shown on each station row.
  const chip = (on, icon, tip) =>
    `<span style="font-size:0.9rem;margin-right:0.15rem;opacity:${on ? "1" : "0.2"};filter:${on ? "none" : "grayscale(1)"}" title="${tip}: ${on ? "available" : "not available"}">${icon}</span>`;

  const rowHtml = result.rows.map((row) => {
    const icon = row.isStart ? "🏁" : row.isFinish ? "🏁" : "⛺";

    const availHtml = (row.isStart || row.isFinish)
      ? ""
      : ` <span style="margin-left:0.4rem">${chip(row.hasWater, "💧", "Water")}${chip(row.hasFood, "🍌", "Food")}${chip(row.hasBags, "🎒", "Drop bag")}</span>`;

    const nameCell = row.isStart || row.isFinish
      ? `<td style="${tdStyle};font-weight:500">${icon} ${row.name}</td>`
      : `<td style="${tdStyle}">${icon} <strong>${row.name}</strong> <span style="color:#4b5563;font-size:0.8rem">· ${row.distKm.toFixed(1)} km</span>${availHtml}</td>`;

    const arriveCell = `<td style="${tdSubStyle};font-variant-numeric:tabular-nums">${fmtSec(row.arriveSec)}</td>`;
    const legTimeCell = `<td style="${tdSubStyle}">${fmtLegTime(row.legSec)}</td>`;
    const waterCell = `<td style="${tdSubStyle}">${row.waterInLeg !== null ? `~${fmtWater(row.waterInLeg)}` : "–"}</td>`;
    const calCell = `<td style="${tdSubStyle}">${row.calInLeg !== null ? `~${row.calInLeg} kcal` : "–"}</td>`;

    let carryCell;
    if (row.isFinish) {
      carryCell = `<td style="${tdSubStyle}">–</td>`;
    } else if (!row.hasWater && !row.hasFood) {
      // Nothing to load up here — make this visually obvious.
      carryCell = `<td style="${tdSubStyle};font-style:italic">skip (no supplies)</td>`;
    } else {
      const warnHtml = row.overCapacity
        ? `<span style="color:#fb923c;margin-left:0.3rem" title="Exceeds your ${fmtWater(settings.carry_capacity_ml)} capacity!">⚠</span>`
        : "";
      const waterColor = row.overCapacity ? "#fb923c" : "#60a5fa";
      const waterPart = row.carryOutWater !== null
        ? `<span style="color:${waterColor};font-weight:600">${fmtWater(row.carryOutWater)}</span>${warnHtml}`
        : `<span style="color:#4b5563">no water</span>`;
      const calPart = row.carryOutCal !== null
        ? `<span style="color:#fb923c;font-weight:500">${row.carryOutCal} kcal</span>`
        : `<span style="color:#4b5563">no food</span>`;
      carryCell = `<td style="${tdStyle};background:#0d1117;border-radius:6px">
        ${waterPart}
        <span style="color:#4b5563;margin:0 0.3rem">·</span>
        ${calPart}
      </td>`;
    }

    const rowBg = row.isStart ? "background:#1a1f35;" : row.isFinish ? "background:#1a1f35;" : "";
    return `<tr style="${rowBg}border-bottom:1px solid #1e2235">${nameCell}${arriveCell}${legTimeCell}${waterCell}${calCell}${carryCell}</tr>`;
  }).join("");

  return summaryHtml + `
    <div style="background:#161b2e;border-radius:10px;overflow:hidden">
      <table style="width:100%;border-collapse:collapse">
        <thead style="background:#0d1117">
          <tr>
            <th style="${thStyle}">Station</th>
            <th style="${thStyle}">Arrive</th>
            <th style="${thStyle}">Leg Time</th>
            <th style="${thStyle}">Water Used</th>
            <th style="${thStyle}">Calories Used</th>
            <th style="${thStyle};color:#e2e8f0">★ Carry Out</th>
          </tr>
        </thead>
        <tbody>${rowHtml}</tbody>
      </table>
    </div>
    <div style="font-size:0.75rem;color:#4b5563;margin-top:0.75rem;padding-left:0.25rem">
      ★ Carry Out = how much to fill/take when leaving each station (water includes 20% buffer, capped at your carry capacity).
      ${result.rows.some(r => r.overCapacity) ? `<span style="color:#fb923c"> ⚠ One or more legs may need more water than your carry capacity — consider adding a station or increasing capacity.</span>` : ""}
    </div>
  `;
}

function initNutritionTab(container, race) {
  // Table is already rendered by buildNutritionTab — we only need to wire events here.
  const bodyEl = container.querySelector("#nutrition-plan-body");

  function getCurrentSettings() {
    return {
      sweat_rate_ml_per_hr: parseInt(container.querySelector("#nutr-sweat")?.value) || 500,
      cal_per_hr: parseInt(container.querySelector("#nutr-cal")?.value) || 250,
      carry_capacity_ml: parseInt(container.querySelector("#nutr-cap")?.value) || 1500,
    };
  }

  function refresh() {
    if (!bodyEl) return;
    const settings = getCurrentSettings();
    bodyEl.innerHTML = nutritionPlanHtml(computeNutritionPlan(race, settings), settings);
  }

  // Live update on any input change
  ["#nutr-sweat", "#nutr-cal", "#nutr-cap"].forEach((sel) => {
    container.querySelector(sel)?.addEventListener("input", refresh);
  });

  // Save to DB
  const saveBtn = container.querySelector("#nutr-save-btn");
  const saveStatus = container.querySelector("#nutr-save-status");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const settings = getCurrentSettings();
      saveBtn.disabled = true;
      saveStatus.style.color = "#4ade80";
      saveStatus.textContent = "Saved ✓";
      try {
        await api.races.update(race.id, { nutrition_settings: settings });
        race.nutrition_settings = settings;
        saveStatus.style.display = "inline";
        setTimeout(() => { saveStatus.style.display = "none"; }, 2000);
      } catch (e) {
        saveStatus.textContent = `Error: ${e.message}`;
        saveStatus.style.color = "#f87171";
        saveStatus.style.display = "inline";
      } finally {
        saveBtn.disabled = false;
      }
    });
  }
}
