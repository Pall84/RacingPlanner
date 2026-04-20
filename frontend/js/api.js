// API client. Defaults to same-origin (empty BASE → relative URLs like
// "/api/fitness/summary"). In production that goes to Netlify, which proxies
// /api/* and /auth/* to the Render backend — see netlify.toml. The proxy
// makes every request look first-party to the browser, which is how we
// keep Safari's ITP from silently dropping the session cookie after OAuth.
//
// In local dev, Vite's dev server is configured with the same proxy (see
// vite.config.js), so relative URLs forward to the local uvicorn.
//
// VITE_API_BASE can still be set explicitly to point at a specific backend
// (useful for preview deploys or when testing a remote backend from local
// frontend) — but production builds should leave it UNSET so relative URLs
// are used.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

async function apiFetch(path, options = {}) {
  const resp = await fetch(API_BASE + path, {
    credentials: "include",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (resp.status === 401) {
    // Send the browser to the backend login endpoint — it will redirect to
    // Strava and ultimately set a session cookie on our API origin.
    window.location.href = `${API_BASE}/auth/login`;
    return null;
  }
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`API ${resp.status}: ${err}`);
  }
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("application/json")) return resp.json();
  return resp;
}

export const api = {
  get: (path) => apiFetch(path),
  post: (path, body) => apiFetch(path, { method: "POST", body: JSON.stringify(body) }),
  patch: (path, body) => apiFetch(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: (path, body) => apiFetch(path, { method: "PUT", body: JSON.stringify(body) }),
  del: (path) => apiFetch(path, { method: "DELETE" }),

  auth: {
    status: () => api.get("/auth/status"),
    logout: () => api.post("/auth/logout", {}),
    updateProfile: (body) => api.patch("/auth/profile", body),
    loginUrl: (inviteCode) =>
      inviteCode ? `${API_BASE}/auth/login?invite=${encodeURIComponent(inviteCode)}` : `${API_BASE}/auth/login`,
  },
  admin: {
    listInvites: (includeUsed = false) => api.get(`/api/admin/invites?include_used=${includeUsed}`),
    createInvite: (body) => api.post("/api/admin/invites", body),
    revokeInvite: (code) => api.del(`/api/admin/invites/${encodeURIComponent(code)}`),
    listUsers: () => api.get("/api/admin/users"),
  },
  activities: {
    list: (limit = 50, offset = 0, racesOnly = false) =>
      api.get(`/api/activities?limit=${limit}&offset=${offset}${racesOnly ? "&races_only=true" : ""}`),
    get: (id) => api.get(`/api/activities/${id}`),
    streams: (id) => api.get(`/api/activities/${id}/streams`),
    kmSplits: (id) => api.get(`/api/activities/${id}/km_splits`),
    laps: (id) => api.get(`/api/activities/${id}/laps`),
    hrZones: (id) => api.get(`/api/activities/${id}/hr_zones`),
    sync: () => api.post("/api/activities/sync", {}),
    updateLap: (id, lapIndex, body) => api.patch(`/api/activities/${id}/laps/${lapIndex}`, body),
    refresh: (id) => api.post(`/api/activities/${id}/refresh`, {}),
    similar: (id) => api.get(`/api/activities/${id}/similar`),
    recoveryContext: (id) => api.get(`/api/activities/${id}/recovery_context`),
    setRaceFlag: (id, isRace) => api.patch(`/api/activities/${id}/race_flag`, { is_race: isRace }),
  },
  fitness: {
    ctlAtlTsb: (start, end) => api.get(`/api/fitness/ctl_atl_tsb${start ? `?start=${start}&end=${end}` : ""}`),
    weekly: (weeks = 52) => api.get(`/api/fitness/weekly?weeks=${weeks}`),
    personalRecords: () => api.get("/api/fitness/personal_records"),
    aerobicEfficiency: (weeks = 26) => api.get(`/api/fitness/aerobic_efficiency?weeks=${weeks}`),
    vo2max: () => api.get("/api/fitness/vo2max"),
    summary: () => api.get("/api/fitness/summary"),
    trainingDistribution: (weeks = 12) => api.get(`/api/fitness/training_distribution?weeks=${weeks}`),
    performanceTrends: (weeks = 52) => api.get(`/api/fitness/performance_trends?weeks=${weeks}`),
    allTimeStats: () => api.get("/api/fitness/all_time_stats"),
    paceZones: () => api.get("/api/fitness/pace_zones"),
    recoveryHistory: (days = 90) => api.get(`/api/fitness/recovery_history?days=${days}`),
    healthCorrelations: (weeks = 26) => api.get(`/api/fitness/health_correlations?weeks=${weeks}`),
  },
  goals: {
    list: () => api.get("/api/goals"),
    create: (body) => api.post("/api/goals", body),
    delete: (id) => api.del(`/api/goals/${id}`),
  },
  sync: {
    status: () => api.get("/api/sync/status"),
    full: () => api.post("/api/sync/full", {}),
    // Re-runs steps 2-9 of the pipeline (streams, laps, metrics, fitness,
    // weekly summaries, PRs, VO2max, Garmin) WITHOUT re-fetching the
    // activity list from Strava. Use when activities are in the DB but
    // streams/metrics are missing — e.g. after a rate-limited sync.
    backfillDetails: () => api.post("/api/sync/backfill_details", {}),
    progressUrl: () => `${API_BASE}/api/sync/progress`,
  },
  garmin: {
    connect: (email, password) => api.post("/api/garmin/connect", { email, password }),
    disconnect: () => api.post("/api/garmin/disconnect", {}),
    status: () => api.get("/api/garmin/status"),
    sync: () => api.post("/api/garmin/sync", {}),
    health: (start, end) => api.get(`/api/garmin/health?start=${start}&end=${end}`),
    latest: () => api.get("/api/garmin/health/latest"),
    trends: (days = 90) => api.get(`/api/garmin/health/trends?days=${days}`),
  },
  races: {
    list: () => api.get("/api/races"),
    get: (id) => api.get(`/api/races/${id}`),
    // Multipart body (GPX upload) — can't use JSON helper
    create: (formData) =>
      fetch(`${API_BASE}/api/races`, { method: "POST", credentials: "include", body: formData }).then(async (r) => {
        if (r.status === 401) {
          window.location.href = `${API_BASE}/auth/login`;
          return null;
        }
        if (!r.ok) {
          const err = await r.text();
          throw new Error(`API ${r.status}: ${err}`);
        }
        return r.json();
      }),
    update: (id, body) => api.patch(`/api/races/${id}`, body),
    predict: (id, body = {}) => api.post(`/api/races/${id}/predict`, body),
    predictionHistory: (id) => api.get(`/api/races/${id}/prediction_history`),
    strategies: (id) => api.get(`/api/races/${id}/strategies`),
    setAidStations: (id, stations) => api.put(`/api/races/${id}/aid_stations`, { stations }),
    readiness: (id) => api.get(`/api/races/${id}/readiness`),
    delete: (id) => api.del(`/api/races/${id}`),
  },
};
