import { api } from "../api.js";

export async function render(container) {
  const [status, syncStatus, garminStatus] = await Promise.all([
    api.auth.status(),
    api.sync.status(),
    api.garmin.status(),
  ]);

  const a = status.athlete || {};
  const s = status.settings || {};

  container.innerHTML = `
    <h1 class="page-title">Settings</h1>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px">
      <!-- Training Settings -->
      <div>
        <div class="chart-section">
          <div class="chart-title">Training Settings</div>
          <div class="form-group">
            <label class="form-label">Max Heart Rate (bpm)</label>
            <input type="number" class="form-input" id="max-hr" value="${s.max_hr}" min="140" max="220">
          </div>
          <div class="form-group">
            <label class="form-label">Resting Heart Rate (bpm)</label>
            <input type="number" class="form-input" id="resting-hr" value="${s.resting_hr}" min="30" max="90">
          </div>
          <div class="form-group">
            <label class="form-label">FTP (watts)</label>
            <input type="number" class="form-input" id="ftp-watts" value="${s.ftp_watts}" min="100" max="500">
          </div>
          <div class="form-group">
            <label class="form-label">HR Zone Method</label>
            <select class="form-input" id="hr-zone-method">
              <option value="karvonen" ${s.hr_zone_method === "karvonen" ? "selected" : ""}>Karvonen (Heart Rate Reserve)</option>
              <option value="percent_max" ${s.hr_zone_method === "percent_max" ? "selected" : ""}>% of Max HR</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Sex (for TRIMP coefficient)</label>
            <select class="form-input" id="trimp-gender">
              <option value="male" ${s.trimp_gender === "male" ? "selected" : ""}>Male (1.92)</option>
              <option value="female" ${s.trimp_gender === "female" ? "selected" : ""}>Female (1.67)</option>
            </select>
          </div>

          <div class="chart-title" style="margin-top:20px">Body Metrics</div>
          <div class="form-group">
            <label class="form-label">Weight (kg)</label>
            <input type="number" class="form-input" id="weight" value="${a.weight || ""}" min="30" max="200" step="0.1">
          </div>
          <div class="form-group">
            <label class="form-label">Height (cm)</label>
            <input type="number" class="form-input" id="height-cm" value="${a.height_cm || ""}" min="100" max="250" step="0.5">
          </div>
          <div class="form-group">
            <label class="form-label">Date of Birth</label>
            <input type="date" class="form-input" id="dob" value="${a.date_of_birth || ""}">
          </div>

          <div style="display:flex;align-items:center;gap:12px;margin-top:16px">
            <button class="btn btn-primary" id="save-settings">Save Settings</button>
            <span id="save-status" style="display:none;color:#4ade80;font-size:0.85rem">Saved ✓</span>
          </div>
          <p style="color:var(--muted);font-size:12px;margin-top:8px">
            Settings are saved per-athlete in the database. Changes take effect on next sync.
          </p>
        </div>
      </div>

      <!-- Sync -->
      <div>
        <div class="chart-section">
          <div class="chart-title">Strava Sync</div>
          <div class="cards-grid" style="margin-bottom:20px">
            <div class="card"><div class="card-label">Total Activities</div><div class="card-value">${syncStatus.activities_count}</div></div>
            <div class="card"><div class="card-label">Streams Pending</div><div class="card-value ${syncStatus.streams_pending > 0 ? 'yellow' : 'green'}">${syncStatus.streams_pending}</div></div>
            <div class="card"><div class="card-label">Metrics Pending</div><div class="card-value ${syncStatus.metrics_pending > 0 ? 'yellow' : 'green'}">${syncStatus.metrics_pending}</div></div>
          </div>

          <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px">
            <button class="btn btn-primary" id="btn-sync-new">Sync New Activities</button>
            <button class="btn btn-secondary" id="btn-full-sync">Full Historical Sync</button>
            <button class="btn btn-secondary" id="btn-backfill-details"
                    ${syncStatus.streams_pending === 0 && syncStatus.metrics_pending === 0 ? "disabled" : ""}
                    title="Re-run metrics + fitness on existing activities without hitting Strava's activity-list endpoint. Use when streams/laps are missing from a previous incomplete sync.">
              Backfill Missing Details${syncStatus.streams_pending > 0 ? ` (${syncStatus.streams_pending})` : ""}
            </button>
          </div>
          <div style="font-size:12px;color:var(--muted);margin-bottom:16px">
            <strong>Backfill</strong> skips the activity-list fetch (the most rate-limited step) and only
            re-pulls streams/laps for activities that are missing them. Runs through metrics, weekly
            summaries, PRs, and VO2max on the updated data.
          </div>

          <div id="sync-log" style="display:none"></div>
        </div>

        <div class="chart-section" style="margin-top:24px" id="garmin-section">
          <div class="chart-title">Garmin Connect</div>
          ${garminStatus.connected ? `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
              <span style="color:#22c55e;font-weight:600">Connected</span>
              <span style="color:var(--muted);font-size:13px">(${garminStatus.email})</span>
            </div>
            ${garminStatus.last_error ? `<div style="background:rgba(239,68,68,0.15);color:#ef4444;padding:8px 12px;border-radius:6px;font-size:13px;margin-bottom:12px">Auth error: ${garminStatus.last_error}</div>` : ""}
            <div class="cards-grid" style="margin-bottom:16px">
              <div class="card"><div class="card-label">Last Sync</div><div class="card-value">${garminStatus.last_sync_date || "Never"}</div></div>
              <div class="card"><div class="card-label">Days Synced</div><div class="card-value">${garminStatus.days_synced}</div></div>
            </div>
            <div style="display:flex;gap:12px">
              <button class="btn btn-primary" id="btn-garmin-sync">Sync Health Data</button>
              <button class="btn btn-secondary" id="btn-garmin-disconnect">Disconnect</button>
            </div>
          ` : `
            <p style="color:var(--muted);font-size:13px;margin-bottom:16px">
              Connect your Garmin account to sync HRV, sleep, body battery, and training readiness data.
            </p>
            <div class="form-group">
              <label class="form-label">Garmin Email</label>
              <input type="email" class="form-input" id="garmin-email" placeholder="your@email.com">
            </div>
            <div class="form-group">
              <label class="form-label">Garmin Password</label>
              <input type="password" class="form-input" id="garmin-password" placeholder="Password">
            </div>
            <div style="display:flex;align-items:center;gap:12px;margin-top:12px">
              <button class="btn btn-primary" id="btn-garmin-connect">Connect</button>
              <span id="garmin-connect-status" style="display:none;font-size:0.85rem"></span>
            </div>
            <p style="color:var(--muted);font-size:11px;margin-top:8px">
              Credentials are encrypted and stored locally. Never sent to third parties.
            </p>
          `}
        </div>

        <div class="chart-section" style="margin-top:24px">
          <div class="chart-title">About</div>
          <p style="color:var(--muted);font-size:13px;line-height:1.6">
            Running Analytics pulls activity streams from Strava and computes:<br><br>
            <strong>Per-activity:</strong> HR zones, TRIMP, Grade Adjusted Pace (Minetti), pace decoupling (aerobic efficiency), cadence distribution, stride length, normalized power (if power data), km splits, workout classification, VDOT.<br><br>
            <strong>Multi-activity:</strong> CTL/ATL/TSB (Fitness/Fatigue/Form), VO2max estimate (Jack Daniels VDOT), training monotony &amp; strain, weekly summaries, personal records, ACWR, readiness score.
          </p>
        </div>
      </div>
    </div>
  `;

  // Save settings to DB
  container.querySelector("#save-settings").addEventListener("click", async () => {
    const btn = container.querySelector("#save-settings");
    const statusEl = container.querySelector("#save-status");
    btn.disabled = true;
    btn.textContent = "Saving…";

    try {
      await api.auth.updateProfile({
        max_hr: parseInt(container.querySelector("#max-hr").value) || null,
        resting_hr: parseInt(container.querySelector("#resting-hr").value) || null,
        ftp_watts: parseFloat(container.querySelector("#ftp-watts").value) || null,
        hr_zone_method: container.querySelector("#hr-zone-method").value,
        trimp_gender: container.querySelector("#trimp-gender").value,
        weight: parseFloat(container.querySelector("#weight").value) || null,
        height_cm: parseFloat(container.querySelector("#height-cm").value) || null,
        date_of_birth: container.querySelector("#dob").value || null,
      });
      statusEl.style.display = "inline";
      setTimeout(() => { statusEl.style.display = "none"; }, 2000);
    } catch (e) {
      alert("Save failed: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Save Settings";
    }
  });

  // Garmin connect
  const btnGarminConnect = container.querySelector("#btn-garmin-connect");
  if (btnGarminConnect) {
    btnGarminConnect.addEventListener("click", async () => {
      const email = container.querySelector("#garmin-email").value.trim();
      const password = container.querySelector("#garmin-password").value;
      if (!email || !password) { alert("Enter both email and password."); return; }

      const statusEl = container.querySelector("#garmin-connect-status");
      btnGarminConnect.disabled = true;
      btnGarminConnect.textContent = "Connecting...";
      statusEl.style.display = "inline";
      statusEl.style.color = "var(--muted)";
      statusEl.textContent = "Authenticating with Garmin...";

      try {
        await api.garmin.connect(email, password);
        statusEl.style.color = "#22c55e";
        statusEl.textContent = "Connected!";
        setTimeout(() => render(container), 1000);
      } catch (e) {
        statusEl.style.color = "#ef4444";
        statusEl.textContent = e.message;
        btnGarminConnect.disabled = false;
        btnGarminConnect.textContent = "Connect";
      }
    });
  }

  // Garmin disconnect
  const btnGarminDisconnect = container.querySelector("#btn-garmin-disconnect");
  if (btnGarminDisconnect) {
    btnGarminDisconnect.addEventListener("click", async () => {
      if (!confirm("Disconnect Garmin? Health data will be kept.")) return;
      await api.garmin.disconnect();
      render(container);
    });
  }

  // Garmin sync
  const btnGarminSync = container.querySelector("#btn-garmin-sync");
  if (btnGarminSync) {
    btnGarminSync.addEventListener("click", async () => {
      btnGarminSync.disabled = true;
      btnGarminSync.textContent = "Syncing...";
      try {
        const result = await api.garmin.sync();
        btnGarminSync.textContent = `Synced ${result.synced_days} days`;
        setTimeout(() => render(container), 1500);
      } catch (e) {
        alert("Garmin sync failed: " + e.message);
        btnGarminSync.disabled = false;
        btnGarminSync.textContent = "Sync Health Data";
      }
    });
  }

  // Sync new
  container.querySelector("#btn-sync-new").addEventListener("click", async (e) => {
    e.target.disabled = true;
    await api.activities.sync();
    startSSE(container);
  });

  // Full sync
  container.querySelector("#btn-full-sync").addEventListener("click", async (e) => {
    if (!confirm("This will re-download all activities and recompute all metrics. It may take a while. Continue?")) return;
    e.target.disabled = true;
    await api.sync.full();
    startSSE(container);
  });

  // Backfill missing details (streams/laps/metrics for activities already in DB)
  const btnBackfill = container.querySelector("#btn-backfill-details");
  if (btnBackfill) {
    btnBackfill.addEventListener("click", async (e) => {
      e.target.disabled = true;
      await api.sync.backfillDetails();
      startSSE(container);
    });
  }
}

function startSSE(container) {
  const log = container.querySelector("#sync-log");
  log.style.display = "block";
  log.textContent = "";

  const es = new EventSource("/api/sync/progress", { withCredentials: true });
  es.onmessage = (e) => {
    if (e.data === ": keepalive") return;
    log.textContent += e.data + "\n";
    log.scrollTop = log.scrollHeight;
    if (e.data === "DONE") {
      es.close();
      container.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  };
  es.onerror = () => {
    log.textContent += "[Connection closed]\n";
    es.close();
    container.querySelectorAll("button").forEach((b) => (b.disabled = false));
  };
}
