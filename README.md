# Financial Market Data Aggregator

A free-tier-friendly market data platform built around six hard requirements:
multi-format ingestion, idempotent normalization, circuit-breaker-protected
resilience, zero-trust RBAC with field masking, a database-enforced audit
trail, and a Redis caching layer — plus AI-powered insights and a React
dashboard with its own "settlement desk" visual identity.

## Architecture at a glance

```
                 ┌─────────────────────┐
   yfinance ───▶ │  Circuit Breaker     │
   CoinGecko ──▶ │  (Redis state +      │──┐
                 │   tenacity retry)    │  │
                 └─────────────────────┘  │
                                           ▼
EOD .csv/.json ─▶ pandas parse ─▶ Normalizer ─▶ Idempotent UPSERT (Postgres)
                                           │            │
                                           │            ▼ (AFTER UPDATE)
                                           │     fn_audit_log() trigger
                                           │            │
                                           ▼            ▼
                                  FastAPI WebSocket  audit_logs
                                  + REST API  ◀── Redis cache-aside
                                           │     (live + static TTL tiers)
                                           ▼
                          JWT (role+uid) ── RBAC ── field masking
                                           │
                                           ▼
                     Terminal-style dashboard (+ AI insights, chat, audit view)
```

### 1. Multi-format ingestion engine
- **Streaming**: `app/ingestion/websocket_feed.py` polls yfinance (equities) and
  CoinGecko (crypto) on a short interval and fans out normalized ticks to every
  connected browser over a native FastAPI `WebSocket` at `/ws/live`.
- **Batch**: `app/ingestion/batch_loader.py` accepts `.csv`/`.json` EOD files via
  `POST /api/ingest/batch`, using `pandas` to handle the column-naming quirks
  different vendors use (`Close` vs `close` vs `Adj Close`, etc).

### 2. Idempotent normalization pipeline
- `app/normalizer.py` maps every source's raw shape into one `NormalizedTick`
  (see `app/schemas.py`), then UPSERTs into Postgres on a natural key
  `(asset_id, source, event_time)` using `ON CONFLICT ... DO UPDATE WHERE
  payload_hash IS DISTINCT FROM EXCLUDED.payload_hash`. A byte-identical retry
  is a true no-op; a legitimate revision (e.g. a restated EOD close) still
  updates — and that update is exactly what requirement 5's audit trigger
  catches.

### 3. Resilience & rate-limiting (circuit breaker)
- `app/circuit_breaker.py` implements a CLOSED → OPEN → HALF_OPEN breaker with
  state in Redis (so it's shared across worker processes), a self-imposed
  request budget per provider, `tenacity` retry-with-backoff for transient
  errors, and a fallback to last-known-good cached data when the circuit is
  open. Live state is at `GET /api/market/circuit-status`.

### 4. Zero-Trust Access Control & Field Masking
- **RBAC**: `app/rbac.py`. Every protected endpoint depends on
  `get_current_user` (decodes & verifies a JWT signed at login) or
  `require_role(...)` / `require_admin`. Roles (`admin`, `client`, `service`)
  are minted into the JWT **only at login** (`app/routers/auth.py`), straight
  from the `users` table — never trusted from anything the client sends.
- **Field masking**: `app/masking.py`. Any response containing
  `account_number` / `routing_number` (see `app/routers/accounts.py`) is
  passed through `mask_record(role=...)`, which redacts those fields to
  `************9104` for every role except `admin`. Masking is enforced
  **per verified role, not per data ownership** — a client looking at their
  own account still gets the masked view, which is what makes this zero
  trust rather than simple ownership-based ACLs.
- Try it: log in as `jane.c` / `client123` and call `GET /api/accounts/me` —
  masked. Log in as `admin` / `admin123` and call the same kind of data —
  full detail.

### 5. Comprehensive Audit Trail & Temporal Logging
- **Enforced at the database layer**, not in application code: `fn_audit_log()`
  in `sql/schema.sql` is a generic PL/pgSQL trigger attached to `price_ticks`
  (`AFTER UPDATE` — i.e. every automated overwrite from an API feed/correction),
  and to `client_accounts` / `account_holdings` (`AFTER INSERT OR UPDATE` —
  manual administrator adjustments). Every fire writes one immutable row to
  `audit_logs` with `changed_at`, `changed_by`, the full `old_data`/`new_data`
  JSONB, the table, and the operation.
- **Actor attribution**: the app sets a transaction-local Postgres setting
  (`SELECT set_config('app.current_actor', :who, true)`, see
  `app/database.py::set_audit_actor`) right before any write that should be
  attributed — the feed name (`feed:yfinance`) for automated overwrites, or
  the admin's username for manual edits — so the trigger can stamp
  `changed_by` correctly. If nothing set it, the trigger falls back to
  `'system'`, so even a stray manual `UPDATE` in `psql` is still caught.
- Read it back via `GET /api/audit/logs` (filterable by table/actor) or
  `GET /api/audit/logs/{table}/{record_id}` for one record's full history —
  both admin-only.

### 6. High-Performance Caching Layer
- `app/cache.py` adds a generic `cache_get_or_set()` cache-aside helper on top
  of the existing Redis client, with two TTL tiers in `app/config.py`:
  `CACHE_TTL_LIVE_SECONDS` (default 5s — tick-level prices) and
  `CACHE_TTL_STATIC_SECONDS` (default 3600s — non-volatile metadata).
- `GET /api/market/profile/{symbol}` is the demonstration endpoint: asset
  sector/market-cap/description/52-week range rarely change, so they're
  cached for an hour and served straight from Redis on every hit after the
  first. The response carries an `X-Cache: HIT`/`MISS` header — watch it flip
  on a second call.
- `GET /api/market/cache-stats` exposes live hit/miss counters (tracked with
  cheap Redis `INCR`s) for the dashboard's caching panel.

### Advanced AI integration
- `app/routers/insights.py`. Three AI-powered surfaces, all functional with
  **zero external cost** out of the box, with an optional upgrade path:
  - `GET /api/ai/insights/{symbol}` — a market commentary narrative. If
    `ANTHROPIC_API_KEY` is set, calls the Claude API directly (grounded only
    in stats computed from your own ingested data — the model is never
    allowed to invent a number). Unset, falls back to a deterministic
    pandas/statistics-driven narrative generator. Cached 5 minutes.
  - `POST /api/ai/chat` — a context-aware assistant that can answer
    "what's AAPL trading at", "what's my balance", "is the circuit breaker
    open" — backed by the same live-data grounding and the same LLM/fallback
    split.
  - `GET /api/ai/anomalies` — rolling z-score outlier detection across all
    symbols' recent prices; flags spikes with no external dependency at all.

### Security & performance (carried over, extended)
- JWT bearer auth now carries verified `uid` + `role` claims (requirement 4).
- Password hashing via stdlib `hashlib.pbkdf2_hmac` (260k iterations, random
  salt) — no extra crypto dependency.
- Redis-backed per-client rate limiting on every public read endpoint.
- Connection pooling (`pool_size=10, max_overflow=20`) and `pool_pre_ping` on
  the async Postgres engine.
- Gzip compression, CORS locked to an explicit origin allowlist.

## Frontend

`frontend/` is a Vite + React app (no Tailwind, no chart library — hand-built
CSS and two small custom SVG/CSS components) built around a deliberate visual
identity rather than a generic dark-mode default:

- **Three typefaces, three jobs**: Fraunces (serif) for the wordmark only,
  Inter for UI copy, IBM Plex Mono for every price/timestamp/code so digits
  stay aligned — the way an actual trading terminal sets type.
- **Signature element**: `components/FlipNumber.jsx` renders live prices as a
  split-flap / departure-board display — only the digits that change flip,
  via a real two-face 3D CSS rotation, not a color flash. `prefers-reduced-motion`
  disables the rotation.
- **Ledger panel** uses a light "paper" surface against the dark desk theme,
  with a rotated stamp badge ("masked · last 4 only" / "unrestricted · admin")
  — a literal, functional readout of which zero-trust branch the API just
  took, not decoration.
- **Structure as information**: section eyebrows are named desk functions
  (Blotter, Ledger, Analyst, Vault, Desk, Registry) that map 1:1 to a backend
  concern, instead of generic numbered steps.
- State is split cleanly: `context/AuthContext.jsx` owns the JWT/role/username
  and an `apiFetch` wrapper that auto-attaches the bearer token and logs out
  on a 401; `hooks/useLiveFeed.js` owns the WebSocket tape; `hooks/usePolling.js`
  is the one generic interval-fetch hook every panel (breakers, cache stats,
  anomalies, audit log) reuses.
- Every component only ever renders what the API actually returned — the
  React role state drives which *panels* mount (e.g. Registry only for
  admin), never what masking is applied to the data inside them. That's still
  decided entirely server-side, by the verified JWT (see requirement 4 above).

## Running it

```bash
cd backend
cp .env.example .env        # edit secrets; AI key is optional
docker compose up --build   # postgres + redis + api on :8000
```

`sql/schema.sql` and `sql/seed.sql` both run automatically on first boot
(Postgres only executes `/docker-entrypoint-initdb.d/*` once, on an empty
data volume — `docker compose down -v` first if you need a clean re-seed).

The dashboard is a Vite + React app (`frontend/`) — no build step is checked
in, so install once and run it:

```bash
cd frontend
cp .env.example .env   # defaults already point at http://localhost:8000
npm install
npm run dev            # http://localhost:5173, hot-reloading
```

`npm run build` produces a static `dist/` you can serve with anything (nginx,
`vite preview`, a CDN) — point `VITE_API_BASE` / `VITE_WS_BASE` in `.env` at
wherever the backend actually lives before building for a real deployment.

### Demo logins (seeded by `sql/seed.sql`)

| username     | password      | role    | what to try                                  |
|--------------|---------------|---------|-----------------------------------------------|
| `admin`      | `admin123`    | admin   | unmasked accounts, audit log viewer, adjust   |
| `jane.c`     | `client123`   | client  | masked own account, AI insights, chat         |
| `ingest-svc` | `service123`  | service | `POST /api/ingest/batch` (machine credential) |

Log in from the dashboard's login screen (it has one-click demo-seat buttons
for all three), or directly:
```bash
curl -X POST http://localhost:8000/auth/login -d "username=admin&password=admin123"
```
The frontend stores the returned JWT/role/username in `localStorage` and
attaches `Authorization: Bearer <token>` to every API call from then on —
no manual token wiring needed once you've signed in through the UI.

## What to build next
- Swap the demo symbol list in `main.py`'s `start_background_ingestion` for a
  configurable, persisted watchlist.
- Add an admin "create account" / "create user" flow (currently seeded only).
- Add Prometheus metrics around the circuit breaker transitions and cache
  hit ratio (the data for both already lives in Redis/Postgres).
- Horizontal scaling: run multiple `uvicorn` workers — every piece of shared
  state (breaker, rate limiter, cache, audit actor) already lives in Redis
  or a per-transaction Postgres setting, not process memory.
