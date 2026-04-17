// Layer 1 smoke test: confirms the frontend can reach the backend /health endpoint.
// Will be replaced by the real app shell in Layer 4.

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

document.getElementById("api-base").textContent = API_BASE;

async function checkHealth() {
  const el = document.getElementById("status");
  try {
    const res = await fetch(`${API_BASE}/health`, { credentials: "include" });
    const data = await res.json();
    el.textContent = `Backend: ${data.status} · DB: ${data.database}`;
    el.style.color = data.database === "connected" ? "#10b981" : "#f59e0b";
  } catch (err) {
    el.textContent = `Backend unreachable: ${err.message}`;
    el.style.color = "#ef4444";
  }
}

checkHealth();
