import { api } from "../api.js";

const PAGE_SIZE = 50;

function decouplingBadge(pct) {
  if (pct === null || pct === undefined) return "<span class='muted'>–</span>";
  if (pct < 5) return `<span class="badge badge-green">${pct.toFixed(1)}%</span>`;
  if (pct < 8) return `<span class="badge badge-yellow">${pct.toFixed(1)}%</span>`;
  return `<span class="badge badge-red">${pct.toFixed(1)}%</span>`;
}

function fmtDist(m) { return m ? `${(m / 1000).toFixed(1)} km` : "–"; }
function fmtTime(sec) {
  if (!sec) return "–";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m} min`;
}

function rowHtml(a) {
  const raceBadge = a.is_race
    ? `<span class="badge" style="background:#3b1f1f;color:#f87171;border:1px solid #5a2a2a;margin-left:6px;font-size:10px">🏁 RACE</span>`
    : "";
  return `
    <tr onclick="navigate('/activity/${a.id}')">
      <td class="muted">${(a.start_date_local || a.start_date || "").slice(0, 10)}</td>
      <td>${a.name || "Run"}${raceBadge}</td>
      <td><span class="badge badge-blue">${a.type || "Run"}</span></td>
      <td>${fmtDist(a.distance_m)}</td>
      <td>${fmtTime(a.moving_time)}</td>
      <td>${a.avg_pace_str || "–"}</td>
      <td>${a.average_heartrate ? Math.round(a.average_heartrate) + " bpm" : "–"}</td>
      <td>${a.cadence_avg ? Math.round(a.cadence_avg) + " spm" : "–"}</td>
      <td>${a.trimp ? a.trimp.toFixed(1) : "–"}</td>
      <td>${decouplingBadge(a.pace_decoupling_pct)}</td>
    </tr>
  `;
}

export async function render(container) {
  container.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px">
      <h1 class="page-title" style="margin:0">Activities</h1>
      <div style="display:flex;gap:12px;align-items:center">
        <div class="filter-group" style="display:inline-flex;background:#1e2235;border:1px solid #2e3348;border-radius:6px;overflow:hidden">
          <button class="filter-btn" data-filter="all" style="padding:6px 14px;background:#2e3348;border:none;color:#e2e8f0;cursor:pointer;font-size:13px">All</button>
          <button class="filter-btn" data-filter="races" style="padding:6px 14px;background:transparent;border:none;color:#8892a4;cursor:pointer;font-size:13px">🏁 Races only</button>
        </div>
        <button class="btn btn-primary" id="btn-sync-new">Sync New</button>
      </div>
    </div>

    <div class="table-section">
      <table class="data-table">
        <thead>
          <tr>
            <th>Date</th><th>Name</th><th>Type</th><th>Dist</th><th>Time</th>
            <th>Pace</th><th>HR</th><th>Cadence</th><th>TRIMP</th><th>Decouple</th>
          </tr>
        </thead>
        <tbody id="activities-tbody"></tbody>
      </table>
      <div id="scroll-sentinel" style="padding:16px;text-align:center;color:var(--muted);font-size:13px">
        Loading…
      </div>
      <div id="scroll-count" style="padding:8px;text-align:center;color:var(--muted);font-size:12px"></div>
    </div>
  `;

  const tbody = container.querySelector("#activities-tbody");
  const sentinel = container.querySelector("#scroll-sentinel");
  const countEl = container.querySelector("#scroll-count");

  // Closure-scoped state — fresh each time the page mounts. No module-level
  // state means that navigating away and back doesn't keep stale data.
  const state = {
    offset: 0,
    total: null,       // null until first page returns
    loading: false,
    done: false,
    observer: null,
    racesOnly: false,
  };

  async function loadNextPage() {
    if (state.loading || state.done) return;
    state.loading = true;
    sentinel.textContent = "Loading…";

    try {
      const data = await api.activities.list(PAGE_SIZE, state.offset, state.racesOnly);
      state.total = data.total;
      state.offset += data.activities.length;

      if (data.activities.length > 0) {
        tbody.insertAdjacentHTML("beforeend", data.activities.map(rowHtml).join(""));
      } else if (state.offset === 0) {
        tbody.innerHTML = `<tr><td colspan="10" class="muted" style="text-align:center;padding:24px">No activities yet. Hit "Sync New" to pull from Strava.</td></tr>`;
      }

      countEl.textContent = `Showing ${state.offset} of ${state.total}`;

      if (state.offset >= state.total || data.activities.length === 0) {
        state.done = true;
        sentinel.textContent = state.total > 0 ? "— end of list —" : "";
        if (state.observer) state.observer.disconnect();
      } else {
        sentinel.textContent = "";  // will be replaced by observer firing next
      }
    } catch (e) {
      sentinel.textContent = `Load failed: ${e.message}. Scroll to retry.`;
      // Allow retry on next scroll trigger.
    } finally {
      state.loading = false;
    }
  }

  // IntersectionObserver fires `loadNextPage` whenever the sentinel enters the
  // viewport. rootMargin lets us pre-load 300px before the sentinel is actually
  // visible, so the user rarely sees a "Loading…" flash.
  state.observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) loadNextPage();
      }
    },
    { rootMargin: "300px" }
  );
  state.observer.observe(sentinel);

  // Load the first page immediately (don't wait for the observer — the sentinel
  // might already be in view on short lists).
  await loadNextPage();

  // Filter toggle — resets pagination when the filter changes.
  function resetAndReload() {
    state.offset = 0;
    state.total = null;
    state.done = false;
    tbody.innerHTML = "";
    countEl.textContent = "";
    if (state.observer) {
      state.observer.disconnect();
      state.observer = new IntersectionObserver(
        (entries) => entries.forEach((en) => en.isIntersecting && loadNextPage()),
        { rootMargin: "300px" }
      );
      state.observer.observe(sentinel);
    }
    loadNextPage();
  }

  container.querySelectorAll(".filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const wantRaces = btn.dataset.filter === "races";
      if (wantRaces === state.racesOnly) return;  // no-op if unchanged
      state.racesOnly = wantRaces;
      // Visual active-state
      container.querySelectorAll(".filter-btn").forEach((b) => {
        const isActive = b === btn;
        b.style.background = isActive ? "#2e3348" : "transparent";
        b.style.color = isActive ? "#e2e8f0" : "#8892a4";
      });
      resetAndReload();
    });
  });

  container.querySelector("#btn-sync-new").addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Syncing…";
    try {
      await api.activities.sync();
    } catch (err) {
      alert(`Sync failed: ${err.message}`);
      e.target.disabled = false;
      e.target.textContent = "Sync New";
      return;
    }
    // Wait a moment for the background task to start writing, then reset and
    // reload. The sync runs asynchronously on the server; new activities will
    // appear on subsequent reloads as the pipeline makes progress.
    setTimeout(() => {
      state.offset = 0;
      state.total = null;
      state.done = false;
      tbody.innerHTML = "";
      countEl.textContent = "";
      if (!state.observer) {
        state.observer = new IntersectionObserver(
          (entries) => entries.forEach((en) => en.isIntersecting && loadNextPage()),
          { rootMargin: "300px" }
        );
        state.observer.observe(sentinel);
      }
      loadNextPage();
    }, 2000);
    setTimeout(() => {
      e.target.disabled = false;
      e.target.textContent = "Sync New";
    }, 5000);
  });
}
