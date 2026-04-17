// Admin page — invite management + user list.
// Only reachable when /auth/status returns is_admin: true (app.js shows the
// nav link conditionally). The backend re-checks admin on every request, so
// a user flipping the DOM can't bypass authorization.
import { api } from "../api.js";

function esc(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

async function loadInvites(container) {
  const list = container.querySelector("#invite-list");
  list.innerHTML = `<div class="loading-spinner">Loading…</div>`;
  try {
    const invites = await api.admin.listInvites(true);
    if (!invites || invites.length === 0) {
      list.innerHTML = `<p class="muted">No invites yet.</p>`;
      return;
    }
    const rows = invites.map((inv) => {
      const used = inv.used_by_athlete_id != null;
      const expired = inv.expires_at && inv.expires_at * 1000 < Date.now();
      const statusBadge = used
        ? `<span class="badge badge-used">used by #${esc(inv.used_by_athlete_id)}</span>`
        : expired
        ? `<span class="badge badge-expired">expired</span>`
        : `<span class="badge badge-ok">active</span>`;
      const actions = used
        ? ""
        : `<button class="btn-danger" data-action="revoke" data-code="${esc(inv.code)}">Revoke</button>`;
      return `
        <tr>
          <td><code>${esc(inv.code)}</code></td>
          <td>${esc(inv.email_hint || "")}</td>
          <td>${statusBadge}</td>
          <td>${fmtDate(inv.created_at)}</td>
          <td>${fmtDate(inv.expires_at)}</td>
          <td>
            <div class="invite-actions">
              <input readonly value="${esc(inv.invite_url)}" class="invite-url-input" />
              <button data-action="copy" data-url="${esc(inv.invite_url)}">Copy</button>
              ${actions}
            </div>
          </td>
        </tr>
      `;
    }).join("");
    list.innerHTML = `
      <table class="data-table">
        <thead>
          <tr><th>Code</th><th>Note</th><th>Status</th><th>Created</th><th>Expires</th><th>Link</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch (e) {
    list.innerHTML = `<p style="color:var(--red)">Could not load invites: ${esc(e.message)}</p>`;
  }
}

async function loadUsers(container) {
  const box = container.querySelector("#user-list");
  box.innerHTML = `<div class="loading-spinner">Loading…</div>`;
  try {
    const users = await api.admin.listUsers();
    if (!users || users.length === 0) {
      box.innerHTML = `<p class="muted">No users yet.</p>`;
      return;
    }
    box.innerHTML = `
      <table class="data-table">
        <thead><tr><th>ID</th><th>Name</th><th>Username</th><th>Admin</th><th>Joined</th></tr></thead>
        <tbody>
          ${users.map((u) => `
            <tr>
              <td>${esc(u.id)}</td>
              <td>${esc(`${u.firstname || ""} ${u.lastname || ""}`.trim())}</td>
              <td>${esc(u.username || "")}</td>
              <td>${u.is_admin ? "✓" : ""}</td>
              <td>${fmtDate(u.created_at)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  } catch (e) {
    box.innerHTML = `<p style="color:var(--red)">Could not load users: ${esc(e.message)}</p>`;
  }
}

export async function render(container) {
  container.innerHTML = `
    <header class="page-header">
      <h1>Admin</h1>
      <p class="muted">Generate invite links and review who's signed up.</p>
    </header>

    <section class="card">
      <h2>Create invite</h2>
      <form id="invite-form" class="inline-form">
        <label>
          Note (optional)
          <input type="text" name="email_hint" placeholder="e.g. alice@example.com" />
        </label>
        <label>
          Expires in
          <select name="expires_in_days">
            <option value="7">7 days</option>
            <option value="14" selected>14 days</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
          </select>
        </label>
        <button type="submit">Generate</button>
      </form>
      <div id="invite-created" class="form-result"></div>
    </section>

    <section class="card">
      <h2>Invites</h2>
      <div id="invite-list"></div>
    </section>

    <section class="card">
      <h2>Users</h2>
      <div id="user-list"></div>
    </section>
  `;

  container.querySelector("#invite-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      email_hint: fd.get("email_hint") || null,
      expires_in_days: parseInt(fd.get("expires_in_days"), 10) || 14,
    };
    const out = container.querySelector("#invite-created");
    out.textContent = "Generating…";
    try {
      const inv = await api.admin.createInvite(body);
      out.innerHTML = `
        <div class="ok">
          Invite created. Share this link:<br>
          <input readonly value="${esc(inv.invite_url)}" class="invite-url-input" />
        </div>
      `;
      e.target.reset();
      loadInvites(container);
    } catch (err) {
      out.innerHTML = `<div style="color:var(--red)">${esc(err.message)}</div>`;
    }
  });

  // Delegated click handler for copy + revoke buttons in the invite table.
  container.querySelector("#invite-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "copy") {
      try {
        await navigator.clipboard.writeText(btn.dataset.url);
        const original = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(() => (btn.textContent = original), 1500);
      } catch {
        /* older browsers */
      }
    } else if (action === "revoke") {
      if (!confirm("Revoke this invite?")) return;
      try {
        await api.admin.revokeInvite(btn.dataset.code);
        loadInvites(container);
      } catch (err) {
        alert(`Revoke failed: ${err.message}`);
      }
    }
  });

  await Promise.all([loadInvites(container), loadUsers(container)]);
}
