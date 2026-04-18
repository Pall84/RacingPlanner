import { api } from "../api.js";
import { fmtTime } from "../util.js";

// Local: profile deliberately omits the "/km" suffix for compactness
// in the pace-zones grid — different display convention than util.fmtPace.
function fmtPace(sec) {
  if (!sec) return "–";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
function calcAge(dob) {
  if (!dob) return null;
  const d = new Date(dob);
  const now = new Date();
  let age = now.getFullYear() - d.getFullYear();
  if (now.getMonth() < d.getMonth() || (now.getMonth() === d.getMonth() && now.getDate() < d.getDate())) age--;
  return age;
}

const GOAL_TYPE_LABELS = {
  weekly_distance: "Weekly Distance",
  annual_distance: "Annual Distance",
  weekly_runs: "Weekly Runs",
  race_time: "Race Goal Time",
};

export async function render(container) {
  container.innerHTML = `<div class="loading-spinner">Loading profile…</div>`;

  const [status, stats, zones, goals, prs, summary] = await Promise.all([
    api.auth.status(),
    api.fitness.allTimeStats(),
    api.fitness.paceZones(),
    api.goals.list(),
    api.fitness.personalRecords(),
    api.fitness.summary(),
  ]);

  const a = status.athlete || {};
  const s = status.settings || {};
  const age = calcAge(a.date_of_birth);

  const prKeys = ["longest_run", "best_5k", "best_10k", "best_half_marathon", "best_marathon", "most_elevation", "fastest_pace"];
  const prLabels = { longest_run: "Longest Run", best_5k: "5k", best_10k: "10k", best_half_marathon: "Half Marathon", best_marathon: "Marathon", most_elevation: "Most Elevation", fastest_pace: "Fastest Pace" };

  container.innerHTML = `
    <!-- Profile Header -->
    <div style="display:flex;align-items:center;gap:1.5rem;margin-bottom:2rem">
      ${a.profile_pic ? `<img src="${a.profile_pic}" style="width:80px;height:80px;border-radius:50%;object-fit:cover;border:3px solid #2e3348">` : `<div style="width:80px;height:80px;border-radius:50%;background:#2e3348;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#8892a4">🏃</div>`}
      <div>
        <h1 style="margin:0 0 0.25rem;font-size:1.75rem">${a.firstname || ""} ${a.lastname || ""}</h1>
        <div style="color:#8892a4;font-size:0.9rem">
          ${[a.city, a.country].filter(Boolean).join(", ") || ""}
          ${a.sex ? ` · ${a.sex === "M" ? "Male" : "Female"}` : ""}
          ${a.weight ? ` · ${a.weight} kg` : ""}
          ${age ? ` · Age ${age}` : ""}
        </div>
        ${stats.first_activity_date ? `<div style="color:#4b5563;font-size:0.8rem;margin-top:0.2rem">Member since ${stats.first_activity_date}</div>` : ""}
      </div>
    </div>

    <!-- All-Time Stats -->
    <div class="cards-grid" style="margin-bottom:2rem">
      <div class="card"><div class="card-label">Total Runs</div><div class="card-value">${stats.total_runs}</div></div>
      <div class="card"><div class="card-label">Total Distance</div><div class="card-value">${stats.total_distance_km >= 1000 ? (stats.total_distance_km / 1000).toFixed(1) + "k" : stats.total_distance_km}</div><div class="card-sub">km</div></div>
      <div class="card"><div class="card-label">Total Time</div><div class="card-value">${stats.total_time_hr >= 100 ? Math.round(stats.total_time_hr) : stats.total_time_hr}</div><div class="card-sub">hours</div></div>
      <div class="card"><div class="card-label">Elevation</div><div class="card-value">${stats.total_elevation_m >= 10000 ? Math.round(stats.total_elevation_m / 1000) + "k" : Math.round(stats.total_elevation_m)}</div><div class="card-sub">m climbed</div></div>
      <div class="card"><div class="card-label">VO2max (VDOT)</div><div class="card-value">${zones.vo2max ?? "–"}</div><div class="card-sub">mL/kg/min</div></div>
      ${summary.garmin_latest?.vo2max_running ? `<div class="card"><div class="card-label">VO2max (Watch)</div><div class="card-value">${summary.garmin_latest.vo2max_running.toFixed(1)}</div><div class="card-sub">Garmin</div></div>` : ""}
      <div class="card"><div class="card-label">Fitness (CTL)</div><div class="card-value">${summary.current_ctl}</div></div>
      <div class="card"><div class="card-label">Streak</div><div class="card-value">${stats.current_streak_days}</div><div class="card-sub">days</div></div>
      <div class="card"><div class="card-label">Readiness</div><div class="card-value ${summary.readiness_score >= 80 ? 'green' : summary.readiness_score < 40 ? 'red' : summary.readiness_score < 60 ? 'yellow' : ''}">${summary.readiness_score}</div><div class="card-sub">${summary.readiness_label}</div></div>
    </div>

    <!-- Goals -->
    <div class="chart-section" style="margin-bottom:2rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div class="chart-title" style="margin:0">Goals</div>
        <button id="add-goal-btn" style="padding:0.3rem 0.8rem;background:transparent;border:1px solid #4f7cff;border-radius:6px;color:#4f7cff;cursor:pointer;font-size:0.8rem">+ Add Goal</button>
      </div>
      <div id="goals-list">
        ${goals.length ? goals.map((g) => goalCard(g)).join("") : `<div style="color:#8892a4;padding:1rem 0;font-size:0.9rem">No goals set yet. Add one to track your progress!</div>`}
      </div>
      <div id="goal-form" style="display:none;margin-top:1rem;background:#1e2235;border-radius:8px;padding:1rem">
        <div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:flex-end">
          <label style="display:flex;flex-direction:column;gap:0.2rem;font-size:0.8rem;color:#8892a4">
            Type
            <select id="goal-type" style="padding:0.35rem 0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.85rem">
              <option value="weekly_distance">Weekly Distance (km)</option>
              <option value="annual_distance">Annual Distance (km)</option>
              <option value="weekly_runs">Weekly Runs</option>
            </select>
          </label>
          <label style="display:flex;flex-direction:column;gap:0.2rem;font-size:0.8rem;color:#8892a4">
            Target
            <input id="goal-target" type="number" min="1" step="1" value="50" style="width:90px;padding:0.35rem 0.5rem;background:#0d1117;border:1px solid #2e3348;border-radius:6px;color:#e2e8f0;font-size:0.85rem">
          </label>
          <button id="goal-save-btn" style="padding:0.35rem 0.8rem;background:#4f7cff;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:0.85rem">Add</button>
          <button id="goal-cancel-btn" style="padding:0.35rem 0.8rem;background:transparent;border:1px solid #2e3348;border-radius:6px;color:#8892a4;cursor:pointer;font-size:0.85rem">Cancel</button>
        </div>
      </div>
    </div>

    <!-- Training Zones -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:2rem">
      <div class="chart-section">
        <div class="chart-title">HR Zones ${zones.hr_zone_method === "karvonen" ? "(Karvonen)" : "(% Max)"}</div>
        <table class="data-table">
          <thead><tr><th>Zone</th><th>Name</th><th>Heart Rate</th></tr></thead>
          <tbody>
            ${zones.hr_zones.map((z) => `
              <tr>
                <td style="font-weight:600;color:#e2e8f0">${z.zone}</td>
                <td>${z.name}</td>
                <td>${z.min_hr > 0 ? z.min_hr : "< " + (z.max_hr + 1)} – ${z.max_hr} bpm</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
        <div style="font-size:0.75rem;color:#4b5563;margin-top:0.5rem">Max HR: ${zones.max_hr} · Resting HR: ${zones.resting_hr}</div>
      </div>
      <div class="chart-section">
        <div class="chart-title">Pace Zones ${zones.vo2max ? `(VDOT ${zones.vo2max})` : ""}</div>
        ${zones.pace_zones.length ? `
          <table class="data-table">
            <thead><tr><th>Zone</th><th>Pace Range</th></tr></thead>
            <tbody>
              ${zones.pace_zones.map((z) => `
                <tr>
                  <td style="font-weight:600;color:#e2e8f0">${z.name}</td>
                  <td>${z.min_pace_str} – ${z.max_pace_str} /km</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
          <div style="font-size:0.75rem;color:#4b5563;margin-top:0.5rem">Based on Jack Daniels VDOT tables</div>
        ` : `<div style="color:#8892a4;padding:1rem 0;font-size:0.9rem">No VO2max estimate available. Sync more training data.</div>`}
      </div>
    </div>

    <!-- Personal Records -->
    <div class="chart-section" style="margin-bottom:2rem">
      <div class="chart-title">Personal Records</div>
      <div class="cards-grid">
        ${prKeys.filter((k) => prs[k]).map((k) => `
          <div class="card" style="cursor:pointer" onclick="navigate('/activity/${prs[k].activity_id}')">
            <div class="card-label">${prLabels[k]}</div>
            <div class="card-value" style="font-size:20px">${prs[k].formatted}</div>
            <div class="card-sub">${prs[k].date}</div>
          </div>
        `).join("") || `<div style="color:#8892a4;padding:1rem 0;font-size:0.9rem">No personal records yet.</div>`}
      </div>
    </div>
  `;

  // Goal form toggle
  const goalForm = container.querySelector("#goal-form");
  container.querySelector("#add-goal-btn").addEventListener("click", () => {
    goalForm.style.display = goalForm.style.display === "none" ? "block" : "none";
  });
  container.querySelector("#goal-cancel-btn").addEventListener("click", () => {
    goalForm.style.display = "none";
  });

  // Add goal
  container.querySelector("#goal-save-btn").addEventListener("click", async () => {
    const type = container.querySelector("#goal-type").value;
    const target = parseFloat(container.querySelector("#goal-target").value);
    if (!target || target <= 0) return;

    try {
      const newGoal = await api.goals.create({ goal_type: type, target_value: target });
      const list = container.querySelector("#goals-list");
      // Remove "no goals" message if present
      if (!goals.length) list.innerHTML = "";
      list.innerHTML += goalCard(newGoal);
      goalForm.style.display = "none";
      goals.push(newGoal);

      // Wire delete for the new card
      wireGoalDeletes(container, goals);
    } catch (e) {
      alert("Failed to add goal: " + e.message);
    }
  });

  // Wire delete buttons
  wireGoalDeletes(container, goals);
}

function goalCard(g) {
  const pct = g.progress?.pct || 0;
  const current = g.progress?.current || 0;
  const target = g.target_value;
  const unit = g.target_unit || "";
  const barColor = pct >= 100 ? "#22c55e" : pct >= 70 ? "#4f7cff" : pct >= 40 ? "#f59e0b" : "#ef4444";

  return `
    <div class="goal-card" data-goal-id="${g.id}" style="background:#1e2235;border-radius:8px;padding:1rem;margin-bottom:0.75rem;display:flex;align-items:center;gap:1rem">
      <div style="flex:1">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:0.4rem">
          <span style="font-size:0.9rem;font-weight:500;color:#e2e8f0">${g.label || g.goal_type}</span>
          <span style="font-size:0.85rem;color:#e2e8f0;font-variant-numeric:tabular-nums">${current} / ${target} ${unit}</span>
        </div>
        <div style="background:#2e3348;border-radius:4px;height:8px;overflow:hidden">
          <div style="background:${barColor};height:100%;width:${Math.min(pct, 100)}%;border-radius:4px;transition:width 0.5s"></div>
        </div>
        <div style="font-size:0.75rem;color:#8892a4;margin-top:0.3rem">${pct >= 100 ? "✓ Goal reached!" : `${pct}% complete`}</div>
      </div>
      <button class="delete-goal-btn" data-goal-id="${g.id}" style="background:transparent;border:none;color:#f87171;cursor:pointer;font-size:1rem;padding:4px" title="Remove goal">✕</button>
    </div>
  `;
}

function wireGoalDeletes(container, goals) {
  container.querySelectorAll(".delete-goal-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.goalId);
      if (!confirm("Remove this goal?")) return;
      try {
        await api.goals.delete(id);
        const card = container.querySelector(`.goal-card[data-goal-id="${id}"]`);
        if (card) card.remove();
        const idx = goals.findIndex((g) => g.id === id);
        if (idx >= 0) goals.splice(idx, 1);
        if (!goals.length) {
          container.querySelector("#goals-list").innerHTML = `<div style="color:#8892a4;padding:1rem 0;font-size:0.9rem">No goals set yet. Add one to track your progress!</div>`;
        }
      } catch (e) {
        alert("Failed to delete goal: " + e.message);
      }
    });
  });
}
