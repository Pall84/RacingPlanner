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
