# Audit Log

A running record of code-audit passes against this repo. Each pass appends
a dated section with scope, findings, and disposition (Fixed / Deferred /
Dismissed). The point is to **stop rediscovering the same issues** on
every pass — anything already triaged here gets pruned from future reports.

## How audits run

1. Launch 4 parallel `Explore` agents in one message with **narrow rubrics**
   (security / concurrency+DB / correctness / data integrity). Cap each at
   10–12 findings. Vague rubrics produce hand-waving; specific rubrics
   surface real bugs.
2. **Verify HIGH findings** against the source before trusting them — agents
   can reference stale code or wrong line numbers.
3. Run the advisor model on the synthesis for severity × effort triage.
   Cut correlated signals before reporting.
4. **Append** to this file with Fixed / Deferred / Dismissed status per item
   before shipping any fixes.

Optional: set up a `scheduled-tasks` cron (weekly) to auto-run step 1.
Human still triages and dismisses.

---

## 2026-04-18 — Audit pass 4

Scope: backend code quality (dead code, duplication, complexity), frontend
code quality (inline styles, duplication), mobile UX, deploy/ops config.
Four parallel `Explore` agents + advisor triage. Advisor cut roughly
half of the proposed findings — mostly large refactors with no observed
user pain ("split buildDetailUI", "de-duplicate sync_streams") and
config polish ("VITE_API_BASE in netlify.toml", "README .gitignore reminder").

### Fixed

| File | Fix | Commit |
|------|-----|--------|
| `frontend/js/util.js` + 4 page files | Extracted duplicated `fmtTime` + `fmtPace` to util.js. `activity_detail.js`, `dashboard.js`, `profile.js`, `races.js` now import the canonical versions. `activity_list.js` keeps its local `fmtTime` because the output shape (`Xh Ym`) is intentionally different from the canonical (`X:YY:ZZ`). | `927ecc1` |
| `backend/app/analytics/formatters.py` (new) + `routes_races.py` + `race_predictor.py` | New `fmt_time` / `fmt_pace` module. Two near-identical copies in routes_races and race_predictor now delegate via thin aliases that preserve each file's nullability convention (None vs "–"). | `6ed9c74` |
| `backend/app/strava/sync.py` lines 165, 245, 302 | Three `except Exception: pass` blocks that were silently swallowing every error including `StravaAuthRevoked`. Narrowed to `(HTTPStatusError, RequestError, KeyError, ValueError)` with structured logging, and explicit `raise` for auth-revocation so the sync halts cleanly and reaches the user's SSE queue with the good reauth message. | `6ed9c74` |
| `backend/app/garmin/sync.py` line 215 | Per-endpoint Garmin catch stays `Exception` (python-garminconnect raises a zoo of undocumented types) but gains an `INFO` log with endpoint name + athlete_id + date + exception class. The failure pattern is now visible in Render logs. | `6ed9c74` |
| `frontend/index.html`, `css/styles.css`, `js/app.js`, plus dashboard + activity_detail | **Baseline mobile responsiveness.** Off-canvas hamburger sidebar at ≤768px, reduced padding, collapsed 2-col grids to 1-col, wide data-tables now scroll horizontally inside their card instead of forcing whole-page scroll. Recovery Context 6-col row collapses to 3 (tablet) → 2 (phone). All rules are additive and gated to ≤768px; desktop layout unchanged. | `87162b6` |

### Deferred (real but not worth the cost right now)

| Finding | Reason |
|---------|--------|
| Split `races.js::buildDetailUI` (~1100 lines) | Advisor: "no observed bug, huge diff, the cost of getting it wrong dwarfs the benefit. Revisit only when you're actively making changes to races.js." |
| De-duplicate `refresh_activity` and `sync_streams` in `strava/sync.py` | Core sync path, real risk of regressing treadmill-correction preservation (which was just stabilized). Revisit if the code actively needs touching. |
| Replace 99+ hardcoded `#8892a4` etc. with `var(--muted)` in `races.js` | No user impact, mechanical but huge. Only worth doing alongside a theme change. |
| Other inline `1fr 1fr` grids on fitness/trends/settings/profile pages on mobile | Still overflow at <=640px. Follow-up pass if those pages get real phone use — the current dashboard + activities + races mobile fix covers the most-visited paths. |
| Loose dep pinning in `pyproject.toml` / missing `uv.lock` | Real gap but builds haven't broken. Add `uv.lock` when setting up a real CI matrix or when a build breaks. |
| No frontend CI (`npm run build` on PR) | Low-volume single-dev project. Deploy fails loudly enough. Add when second contributor lands. |

### Dismissed

| Finding | Why not |
|---------|---------|
| Auth pattern `_get_athlete_id` vs `Depends(get_current_athlete)` | Both work. Unifying is a large diff with zero user impact — cosmetic. |
| `_int_or_none` helper only in `garmin/sync.py` | Not worth promoting until a second callsite needs it. |
| `predict_race_time` is >100 lines | Advisor confirmed: it's sequential step code, not hidden complexity. Length is honest. |
| Dockerfile `sh -c` entrypoint "fragility" | USER directive is correct today; "what if someone moves it" is hypothetical. Fine. |
| `VITE_API_BASE` not in `netlify.toml` | Value is set via Netlify env UI + GitHub Actions. Adding to `netlify.toml` would be a placeholder, not useful. |
| `pre-commit autoupdate`, render.yaml `LOG_LEVEL` clarity, docker-compose password comment, README `.gitignore` reminder, CORS debug logs | Advisor called these noise. Dismissed. |
| Mobile polish items (modal width, aid-station inputs, chart min-height, strategy modal) | "Polish on top of a foundation that doesn't exist yet" — ship the foundation first. |
| Banner/alert HTML duplication + card patterns (~6 findings) | Real but mechanical. Worth a dedicated refactor pass when actively editing those pages; not enough pain to justify standalone work. |

---

## 2026-04-18 — Audit pass 3

Scope: frontend security + resource lifecycle, backend input validation,
external API resilience, observability. Four parallel `Explore` agents
with narrow rubrics that explicitly skipped the "dismissed" items from
pass 2. Advisor triage cut the ship list roughly in half — trimmed a lot
of observability-logging proposals as "add when debugging, not proactively".

### Fixed

| File | Fix | Commit |
|------|-----|--------|
| `backend/app/main.py` `/health` | Returns HTTP 503 when DB is unreachable instead of 200 with `database: error`. Render's liveness probe now actually detects DB outages and restarts the container. Advisor flagged this as the most operationally important item of the pass. | `80c9fe0` |
| `backend/app/api/routes_auth.py` `ProfileUpdateRequest` | Pydantic `Field(..., ge=N, le=M)` bounds on weight (25-250 kg), height (100-230 cm), max_hr (100-250), resting_hr (25-120), ftp_watts (>0 <=600). Prevents garbage like weight=-1 corrupting downstream TRIMP/VDOT math. | `80c9fe0` |
| `backend/app/api/routes_activities.py` | `limit` bounded 1-500, `offset` 0-100k. `LapCorrectionRequest` distance >0, elevation >=0. Prevents DoS via huge limit and pace crashes on negative lap corrections. | `80c9fe0` |
| `backend/app/api/routes_fitness.py`, `routes_garmin.py` | `weeks` 1-260 (5yr cap), `days` 1-3650 (10yr cap) on all query params. Prevents `timedelta` overflow on absurd values and negative-date underflow on -N. | `80c9fe0` |
| `backend/app/api/routes_goals.py` `GoalCreateRequest` | `target_value > 0`, `<= 1_000_000`. Line 73 divides `current / target_value * 100` — zero target would crash, negative gives nonsense progress %. | `80c9fe0` |
| `backend/app/api/routes_races.py` `RaceUpdateRequest` | `actual_time_sec > 0 <= 48h`. Defensive date parse in readiness endpoint — wraps `date.fromisoformat(race.date[:10])` in try/except; a corrupted race.date was crashing with a raw 500. | `80c9fe0` |
| Frontend XSS — new `frontend/js/util.js` with `escapeHtml()` helper | Applied to activity.name in detail + list, race.name + location + date in list + detail, aid station name + notes in edit + display + Leaflet popup. All previously interpolated raw into innerHTML — malicious activity/race/station names could trigger XSS. | `72c486b` |
| `backend/app/strava/auth.py` + `app/api/_errors.py` + `routes_sync.py` | New `StravaAuthRevoked` domain exception. `get_valid_token` catches 400/401 from refresh_access_token and raises it. `translate_strava_error` maps it to 401 with "please log out and log back in" message. SSE background task surfaces the same friendly message. Previously was a raw "HTTPStatusError" in the SSE error path. | `058d409` |
| `backend/app/garmin/client.py` `login()` | Wrapped `asyncio.to_thread(_do_login)` in `asyncio.wait_for(timeout=30)`. python-garminconnect has no built-in HTTP timeout; a hung login could starve Render's small thread pool and block concurrent requests. Caveat documented: wait_for cancels the coroutine but can't cancel the Python thread (no thread-cancellation primitive). | `058d409` |

### Deferred (real but lower priority — revisit when triggered)

| Finding | Reason |
|---------|--------|
| Strava sync page-level transaction boundaries | Real — if page 3 of 10 crashes mid-insert, state can be inconsistent. But no observed corruption and the fix is complex (`begin_nested` savepoints). Revisit if we see real user reports. |
| Strava 429 retry counter | Current 30s backoff cap already prevents runaway recursion. Defensive only. |
| `setTimeout` tracking in `activity_list.js` sync reset | Theoretical race — navigating during the 2s window. Low impact. |
| OAuth token-exchange timeout 20s → 10s | No user pain observed. Keep current. |

### Dismissed (already handled or not worth fixing)

| Finding | Why it's not a bug |
|---------|---------------------|
| `confirm("Delete $name?")` with unescaped name | `confirm()` renders plain text, not HTML. Not an XSS vector. |
| Chart.js / Leaflet instance leaks | Checked — `destroyChart()` and `_map.remove()` are called on re-render. Clean. |
| Event listener accumulation across navigations | Checked — observers are disconnected in `resetAndReload()`. Clean. |
| Observability context log proposals (7 findings from agent D) | Advisor guidance: "add logs when actually debugging, not proactively. Proactive logs are noise; reactive logs are signal." Dismiss. |
| 429 jitter, auth exception narrowing, stream-auth-swallow, weather 5s timeout | None have user-facing evidence. Nice-to-haves. |
| Partial Garmin per-day error isolation (already has try/except) | Current pattern is OK; agent wanted per-endpoint context logs which is "add when debugging" territory. |
| GPX XXE / entity-expansion | `xml.etree.ElementTree` disables external entities by default since Python 3.7.1. Safe. |
| Admin.js user list rendering | Already uses `esc()` helper. Clean. |

---

## 2026-04-18 — Audit pass 2

Scope: backend security, concurrency/DB, analytics correctness, data integrity +
migrations. Four parallel `Explore` agents plus advisor triage.

### Fixed

| File | Fix | Commit |
|------|-----|--------|
| `backend/app/main.py:86` | Exception handler no longer echoes `str(exc)` to client — was risking leak of DB URLs, refresh tokens, stack fragments. Full trace stays in Render logs. | `960010d` |
| `backend/app/api/routes_activities.py:422, 487` | Defense-in-depth: added `athlete_id` filter to follow-up queries in `/refresh`. Outer ownership check already gates access, but a future refactor could silently cross-leak. | `960010d` |
| `backend/app/analytics/compute_pipeline.py:143` | Replaced KmSplit N+1 insert loop with a single multi-row `pg_insert`. A full backfill was running 5k-20k individual INSERT round-trips; now one per activity. | `ed42b91` |
| `backend/alembic/versions/0005_perf_indexes.py` + `schema.py` | Added `idx_activity_metrics_workout_type`. Classification step filters `WHERE workout_type IS NULL` on every full sync. | `ed42b91` |
| `backend/app/analytics/weather.py` | Guard against empty `race_temps` slices — Open-Meteo can return hours that are all None for far-future dates. Returns None cleanly instead of dividing by zero. | `ed42b91` |
| `backend/app/analytics/race_predictor.py` | Skip rows with `avg_gap_sec_per_km == None` in `_compute_gap_for_rows`. Early-return None in `_recent_race_predict` when `target_dist_m <= 0`. Both defensive — current callers don't trigger the bugs, but a future filter or argument change would. | `ed42b91` |

### Deferred (revisit when triggered)

| Finding | Reason |
|---------|--------|
| Missing `ondelete="CASCADE"` on most `ForeignKey("athlete.id")` (schema.py) | There's no delete-athlete endpoint yet. Cascade strategy (hard vs soft delete) needs design. Revisit with the account-deletion feature. |
| `PersonalRecord.activity_id` lacks `ondelete` | PRs are rebuilt from scratch on every full sync via `update_personal_records()`. Orphans get overwritten. Low impact. |
| `Race.linked_activity_id` lacks `ondelete="SET NULL"` | Nullable field; app already treats null as "not linked". Cosmetic only. |
| Indexes on `DailyFitness.date`, `GarminDailyHealth.date` | Queries filter by `athlete_id` first, hitting existing indexes. Sub-ms at current scale. Revisit if user count ≥ 10×. |

### Dismissed (known fine — don't re-report)

| Finding | Why it's not a bug |
|---------|---------------------|
| SSE queue memory leak (`routes_sync.py`) | Already fixed in `6eb7aa6` — agent read stale state. |
| Garmin sync TOCTOU on date pre-check | The pre-check is a freshness gate, not ownership. `ON CONFLICT (date, athlete_id) DO UPDATE` handles any race. |
| CORS `SameSite=None` | Required for cross-origin auth (Netlify → Render). Removing would break login. |
| `sync_activities` doesn't explicitly set `is_race=0` on new rows | Column has `server_default="0"`. Explicit set is cosmetic. |
| Off-by-one in `gpx_parser._compute_segment_stats` | Code is correct; agent flagged a defensive comment opportunity. |

---

## 2026-04-17 — Audit pass 1 (analytics + codebase deep analysis)

Scope: analytics calculations, Strava/Garmin import opportunities, general
code review. Initiated by user ("can you do a deep analyzes of the code").

### Fixed

| Finding | Commit |
|---------|--------|
| TRIMP gender scalar/exponent pairing — was using 0.64 with both exponents; female TRIMP underweighted ~25-34% | `5734068` |
| Sub-Z1 HR time was silently dropped from zone totals and TRIMP | `5734068` |
| GPX raw elevation gain/loss overestimated 30–100% on noisy tracks; replaced with pivot/extremum min-delta algorithm (1.5 m threshold) | `5734068` |
| Per-activity VDOT applied to easy runs — gated to `avg HR ≥ 85% max` | `5734068` |
| LT pace unit bug — Garmin returns `speed` field as seconds-per-meter, not m/s. Predictions were 20×-50× too slow | `7a54681` |
| Frontend method labels — `lt_pace` and `watch_vo2max` were mislabeled "Riegel Scaling" | `7a54681` |

### Deferred

| Finding | Reason |
|---------|--------|
| Riegel exponent 1.06 for 5K→marathon is known-optimistic | Literature varies 1.06–1.15; distance-dependent exponent is a reasonable upgrade but touches the prediction core. Revisit after more backtest data. |
| VDOT marathon denominator approximation (0.8 vs 0.8088) | ~1% error. Not worth the algebra. |
| Course simulation weight blending of Z2 + Z4 runs | Subjective accuracy tradeoff; current pass-through works acceptably. |

### Dismissed

| Finding | Why not |
|---------|---------|
| Pace decoupling midpoint by distance vs time | Minor; both valid approaches. |
| PR distance windows (±10% on 5K) | Design choice, not a bug. |
| Strava altitude stream elevation loss uses naive sum | Strava pre-smooths altitude server-side. |
| Hill Score from Garmin | Minetti course penalty already handles terrain. |
| Strava segment_efforts as implicit PRs | Complex Segments API integration for uncertain lift. |

---

## 2026-04-17 — Audit pass 0 (original deep analysis, pre-predictor-v2)

Five bugs found via structured audit:

| Finding | Commit |
|---------|--------|
| SQL subquery missing `AS recent30` alias under Postgres | `6eb7aa6` |
| Invite claim race condition — replaced check-then-set with atomic conditional UPDATE | `6eb7aa6` |
| Garmin sync float-to-int coercion (`_int_or_none` helper) | `6eb7aa6` |
| `.limit(30)` no-op on bare aggregate — ported subquery pattern from compute_pipeline | `6eb7aa6` |
| `_progress_queues` never freed — SSE generator now pops in finally block | `6eb7aa6` |
