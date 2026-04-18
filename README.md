# RacingPlanner

Multi-user running analytics, race prediction, and training planning — Strava + Garmin, deployed to the web.

- **Frontend**: vanilla-JS SPA on [Netlify](https://www.netlify.com/)
- **Backend**: FastAPI on [Render](https://render.com/)
- **Database**: Postgres on [Neon](https://neon.tech/)
- **Auth**: Strava OAuth, invite-only signup, signed session cookies
- **Integrations**: Strava (required), Garmin Connect (optional, per-user encrypted credentials)

Repo: https://github.com/Pall84/RacingPlanner

## Local development

### Prerequisites

- Docker + Docker Compose
- Node 20+ (for the frontend)
- A Strava API app — https://www.strava.com/settings/api (the callback URL must be `http://localhost:8000/auth/callback`)
- Your Strava athlete ID (visible in the URL of your Strava profile page)

### Setup

```bash
# 1. Clone
git clone https://github.com/Pall84/RacingPlanner.git
cd RacingPlanner

# 2. Fill in .env
cp .env.example .env
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_hex(32))" >> .env
python -c "import secrets; print('GARMIN_MASTER_KEY=' + secrets.token_hex(32))" >> .env
# Edit .env: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, ADMIN_ATHLETE_ID

# 3. Start backend + Postgres
docker compose up --build

# 4. In a second terminal, start the frontend
cd frontend
npm install
npm run dev  # http://localhost:5173
```

First time you log in with the Strava account matching `ADMIN_ATHLETE_ID`, you're promoted to admin. The Admin nav item appears. Generate invite links from there; anyone holding a valid invite can sign up.

### Running tests

```bash
docker exec racingplanner-backend-1 python -m pytest -v
```

## Deployment

Three services, each free-tier-friendly. High-level order: database first, then backend, then frontend.

### 1. Neon (database)

1. Create a project at https://neon.tech — pick the region closest to your Render region.
2. Copy the connection string. **Use the async form**: replace `postgresql://` with `postgresql+asyncpg://`.
3. Save it for the next step.

### 2. Render (backend)

Two options:

**Option A — Blueprint (recommended).** In the Render dashboard, **New +** → **Blueprint**, point it at this repo. Render reads `render.yaml` and provisions the service. Fill in every `sync: false` env var in the UI.

**Option B — Manual.** New Web Service, select this repo, Docker runtime, `./backend/Dockerfile` as the Dockerfile path, `./backend` as the Docker context. Health check: `/health`. Set the same env vars listed in `render.yaml`.

Required env vars (screens will prompt you):

| Variable | Source |
|-|-|
| `DATABASE_URL` | Neon connection string (asyncpg variant) |
| `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GARMIN_MASTER_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | Strava API app |
| `STRAVA_REDIRECT_URI` | `https://<service>.onrender.com/auth/callback` — also add this as an allowed callback in your Strava app |
| `FRONTEND_ORIGIN` | The Netlify URL you'll get in step 3 (comma-separated if multiple) |
| `ADMIN_ATHLETE_ID` | Your Strava athlete ID |
| `SENTRY_DSN` | Optional |

Render will build, apply Alembic migrations, and boot `uvicorn`. First request after 15 min of idleness takes ~30s on the free tier — expected.

### 3. Netlify (frontend)

1. In Netlify, **Add new site** → **Import from Git** → pick this repo.
2. Build settings are auto-detected from `frontend/netlify.toml`:
   - Base directory: `frontend`
   - Build command: `npm ci && npm run build`
   - Publish directory: `dist` (resolves to `frontend/dist`)
3. Add environment variable `VITE_API_BASE` = `https://<your-render-service>.onrender.com`.
4. Deploy. Once live, copy the URL back into Render's `FRONTEND_ORIGIN` and redeploy the backend (so CORS accepts it).

### 4. GitHub Actions (CI/CD)

Push to `main` automatically triggers deploys if secrets are set:

| Secret | Where to get it |
|-|-|
| `RENDER_DEPLOY_HOOK` | Render → Service → Settings → Deploy Hook |
| `NETLIFY_AUTH_TOKEN` | Netlify → User settings → Applications → Personal access tokens |
| `NETLIFY_SITE_ID` | Netlify → Site settings → General → Site information |
| `VITE_API_BASE` | Your Render URL (used at frontend build time) |

Add them under **Settings → Secrets and variables → Actions**. Without them, CI still runs tests; deploys are skipped with a warning (you can always deploy by pushing to the Git branches Netlify/Render track natively).

### 5. Recommended branch protection

In **Settings → Branches → Rule for `main`**:
- Require a PR before merging
- Require the `Backend CI / lint-and-test` check to pass
- Forbid force-pushes

## Strava webhooks (optional but recommended)

Without webhooks, users click **Sync New** on the Activities page to pull
new activities from Strava. With webhooks configured, the backend receives
a push from Strava within seconds of each activity being created, updated,
or deleted, and refreshes automatically.

Setup (run once after deploy):

```bash
# 1. Generate a verify token and set it on Render (and in local .env).
python -c "import secrets; print(secrets.token_urlsafe(24))"

# 2. After Render has picked up the new env var and redeployed, register
#    the subscription with Strava. This ALSO triggers a GET handshake
#    against the callback URL, so the backend must already have the
#    same verify token set — otherwise the create call fails with 400.
python backend/scripts/manage_strava_subscription.py create \
    --callback-url https://<your-backend>.onrender.com/api/webhooks/strava

# 3. Optional: confirm it's registered
python backend/scripts/manage_strava_subscription.py list

# Revoke (stops all webhook events; users fall back to manual sync)
python backend/scripts/manage_strava_subscription.py delete <id>
```

One subscription per Strava app is allowed. If you change the callback URL
you need to `delete` the old one before `create`-ing a new one.

## Architecture

```
┌────────────────┐      HTTPS       ┌────────────────┐      asyncpg       ┌────────────────┐
│  Netlify CDN   │ ───────────────► │  Render (API)  │ ───────────────►  │  Neon Postgres │
│  (static SPA)  │   credentials    │   FastAPI +    │                    │                │
│                │    include       │   uvicorn      │                    │                │
└────────────────┘                  └────────────────┘                    └────────────────┘
```

- **Auth**: Strava OAuth → backend sets an `itsdangerous`-signed session cookie (`rp_session`). Cross-origin cookies use `SameSite=None; Secure` in production, `SameSite=Lax` locally.
- **Invites**: admin generates codes; new signups round-trip the code through Strava OAuth via a signed state token. Non-invited Strava logins are rejected unless the athlete row already exists (returning users) or matches `ADMIN_ATHLETE_ID` (bootstrap).
- **Garmin credentials**: encrypted per-user via HKDF-derived Fernet keys (master key stays in env vars, never on disk; each athlete has a 16-byte salt in the DB).
- **Strava rate limits**: per-user token bucket (~35/15min) on top of an app-wide hard cap (90/15min), so one heavy syncer cannot starve others.
- **Background jobs**: FastAPI `BackgroundTasks` in-process. SSE progress via an in-memory `asyncio.Queue`. Single-instance Render deploy — no Redis/Celery needed.

## Pre-commit hooks (recommended)

```bash
pip install pre-commit
pre-commit install
```

Installs `gitleaks` (blocks committed secrets), `ruff` lint + format for backend, and a handful of file-hygiene checks.

## Project layout

```
.
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini, alembic/       Schema migrations
│   ├── app/
│   │   ├── main.py                 FastAPI entrypoint + CORS + Sentry
│   │   ├── config.py               Settings, get_athlete_settings()
│   │   ├── database.py, security.py
│   │   ├── api/                    Route modules (auth, admin, activities, fitness, sync, races, goals, garmin)
│   │   ├── analytics/              Pure analytics (metrics, classification, fitness, race prediction, GPX, pipeline)
│   │   ├── strava/                 OAuth + per-athlete-rate-limited API client
│   │   ├── garmin/                 Per-user-encrypted Garmin Connect client
│   │   └── models/schema.py        SQLAlchemy models (Postgres)
│   └── tests/                      pytest (security, per-user crypto)
├── frontend/
│   ├── index.html
│   ├── netlify.toml, _redirects
│   ├── package.json                Vite (dev server + build)
│   ├── css/styles.css
│   └── js/
│       ├── api.js                  Cross-origin API client; uses VITE_API_BASE
│       ├── app.js                  Router + admin nav gating + invite-aware signup
│       ├── charts.js, map.js
│       └── pages/                  dashboard, activities, fitness, trends, races, profile, settings, admin
├── docker-compose.yml              Local dev: postgres + backend
├── render.yaml                     Render infra-as-code
├── .github/
│   ├── workflows/
│   │   ├── backend-ci.yml          Ruff + pytest on PR
│   │   └── deploy.yml              Render + Netlify deploy on main
│   └── dependabot.yml
└── .pre-commit-config.yaml
```

## License

MIT — see [LICENSE](LICENSE).
