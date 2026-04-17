import { api, API_BASE } from "./api.js";

const content = document.getElementById("content");
const athleteName = document.getElementById("athlete-name");
const logoutBtn = document.getElementById("logout-btn");
const adminNavItem = document.getElementById("nav-admin-item");

// ── Routing ──────────────────────────────────────────────────────────────────
const routes = {
  "/": "dashboard",
  "/activities": "activity_list",
  "/fitness": "fitness",
  "/trends": "trends",
  "/races": "races",
  "/profile": "profile",
  "/settings": "settings",
  "/admin": "admin",
};

async function loadPage(path) {
  // Mark active nav link
  document.querySelectorAll("#sidebar ul li a").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("href") === path);
  });

  content.innerHTML = `<div class="loading-spinner">Loading...</div>`;

  let moduleName = routes[path];
  if (!moduleName && path.startsWith("/activity/")) moduleName = "activity_detail";
  if (!moduleName && path.startsWith("/races/")) moduleName = "races";
  if (!moduleName) moduleName = "dashboard";

  try {
    const mod = await import(`./pages/${moduleName}.js`);
    const activityId = path.startsWith("/activity/") ? path.split("/").pop() : null;
    const raceId = path.startsWith("/races/") ? path.split("/").pop() : null;
    await mod.render(content, activityId || raceId);
  } catch (e) {
    content.innerHTML = `<div class="loading-spinner" style="color:var(--red)">Error loading page: ${e.message}</div>`;
    console.error(e);
  }
}

function navigate(path, pushState = true) {
  if (pushState) history.pushState({}, "", path);
  loadPage(path);
}

// Intercept same-origin link clicks
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a) return;
  const href = a.getAttribute("href");
  if (!href || href.startsWith("http") || href.startsWith("#")) return;
  e.preventDefault();
  navigate(href);
});

window.addEventListener("popstate", () => navigate(location.pathname, false));

window.navigate = navigate;

// ── Signup page (from invite link) ───────────────────────────────────────────
// Invite flow: admin shares <site>/signup?invite=XXX. We intercept that
// path BEFORE calling /auth/status (which would otherwise redirect to Strava
// without the invite) and push the user through login-with-invite.
function handleSignupPage() {
  if (location.pathname !== "/signup") return false;
  const params = new URLSearchParams(location.search);
  const invite = params.get("invite");
  if (invite) {
    window.location.href = api.auth.loginUrl(invite);
  } else {
    content.innerHTML = `
      <div class="loading-spinner" style="color:var(--red)">
        Signup requires an invite link. Ask the admin for one.
      </div>`;
  }
  return true;
}

// ── Boot ─────────────────────────────────────────────────────────────────────
async function init() {
  if (handleSignupPage()) return;

  let status = null;
  try {
    status = await api.auth.status();
  } catch (e) {
    content.innerHTML = `
      <div class="loading-spinner" style="color:var(--red)">
        Could not reach backend (${API_BASE}). ${e.message}
      </div>`;
    return;
  }

  if (!status || !status.authenticated) {
    // Full redirect to the backend login endpoint.
    window.location.href = api.auth.loginUrl();
    return;
  }

  const a = status.athlete;
  athleteName.textContent = `${a.firstname || ""} ${a.lastname || ""}`.trim() || "Athlete";

  if (a.is_admin && adminNavItem) {
    adminNavItem.style.display = "";
  }

  logoutBtn.addEventListener("click", async () => {
    await api.auth.logout();
    window.location.href = api.auth.loginUrl();
  });

  navigate(location.pathname, false);
}

init();
