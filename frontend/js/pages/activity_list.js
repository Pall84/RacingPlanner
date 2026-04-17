import { api } from "../api.js";

const PAGE_SIZE = 50;
let _offset = 0;
let _total = 0;

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

async function loadPage(container, offset) {
  _offset = offset;
  const data = await api.activities.list(PAGE_SIZE, offset);
  _total = data.total;

  const rows = data.activities.map((a) => `
    <tr onclick="navigate('/activity/${a.id}')">
      <td class="muted">${(a.start_date_local || a.start_date || "").slice(0, 10)}</td>
      <td>${a.name || "Run"}</td>
      <td><span class="badge badge-blue">${a.type || "Run"}</span></td>
      <td>${fmtDist(a.distance_m)}</td>
      <td>${fmtTime(a.moving_time)}</td>
      <td>${a.avg_pace_str || "–"}</td>
      <td>${a.average_heartrate ? Math.round(a.average_heartrate) + " bpm" : "–"}</td>
      <td>${a.cadence_avg ? Math.round(a.cadence_avg) + " spm" : "–"}</td>
      <td>${a.trimp ? a.trimp.toFixed(1) : "–"}</td>
      <td>${decouplingBadge(a.pace_decoupling_pct)}</td>
    </tr>
  `).join("");

  const startItem = offset + 1;
  const endItem = Math.min(offset + PAGE_SIZE, _total);

  container.querySelector("#activities-tbody").innerHTML = rows || `<tr><td colspan="10" class="muted" style="text-align:center;padding:24px">No activities found</td></tr>`;
  container.querySelector("#pagination-info").textContent = `${startItem}–${endItem} of ${_total}`;
  container.querySelector("#btn-prev").disabled = offset === 0;
  container.querySelector("#btn-next").disabled = offset + PAGE_SIZE >= _total;
}

export async function render(container) {
  container.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px">
      <h1 class="page-title" style="margin:0">Activities</h1>
      <button class="btn btn-primary" id="btn-sync-new">Sync New</button>
    </div>

    <div class="table-section">
      <table class="data-table">
        <thead>
          <tr>
            <th>Date</th><th>Name</th><th>Type</th><th>Dist</th><th>Time</th>
            <th>Pace</th><th>HR</th><th>Cadence</th><th>TRIMP</th><th>Decouple</th>
          </tr>
        </thead>
        <tbody id="activities-tbody">
          <tr><td colspan="10" class="muted" style="text-align:center;padding:24px">Loading...</td></tr>
        </tbody>
      </table>
      <div class="pagination">
        <button id="btn-prev" disabled>← Prev</button>
        <span id="pagination-info">–</span>
        <button id="btn-next" disabled>Next →</button>
      </div>
    </div>
  `;

  await loadPage(container, 0);

  container.querySelector("#btn-prev").addEventListener("click", () => loadPage(container, _offset - PAGE_SIZE));
  container.querySelector("#btn-next").addEventListener("click", () => loadPage(container, _offset + PAGE_SIZE));

  container.querySelector("#btn-sync-new").addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Syncing...";
    await api.activities.sync();
    setTimeout(() => loadPage(container, 0), 2000);
    setTimeout(() => { e.target.disabled = false; e.target.textContent = "Sync New"; }, 5000);
  });
}
