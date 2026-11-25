# Algora architecture (Phase 1: foundation)

Algora lets Indian retail traders build no-code strategies, backtest them, paper trade, and later go live
through their own broker on NSE, NFO and MCX. Phase 1 builds the foundation everything else plugs into.

```
 GitHub Pages (static site, unchanged)          Algora API (FastAPI)                    Postgres (+TimescaleDB)
 index.html, algora-*.js  ── HTTPS + JWT ──▶   auth · strategies · accounts · risk ──▶ users, strategies(+versions),
   localStorage export ──── POST /import ──▶    backtests · import · audit             backtest_runs, audit_log, ...
                                                      │                                 Redis: rate limits (later: queues)
                                                      ▼
                                              algora_engine (pure Python library)
                                              DSL · Trader loop · fills · charges · lots · calendar · metrics
                                                      ▲
                     Phase 3 paper engine ────────────┤  same Trader, simulated fills, live candles
                     Phase 4 live executor ───────────┘  same Trader, broker-hosted, limit orders only
```

## Components

### `engine/` — `algora_engine`
A dependency-light library (only pydantic). It has no I/O, no database and no network, so the exact same code
runs in the API, in a paper-trading worker and inside a broker's infrastructure.

| Module | Role |
| --- | --- |
| `dsl` | Strategy schema v1 (pydantic, `extra="forbid"`). Exported to `engine/schema/strategy.v1.json`; a test fails if the committed schema drifts. `load_spec` rejects documents without `schema_version` and is where future migrations (v1 → v2) go. |
| `templates` | Builders that *produce DSL*. First Candle Cross is just two `crosses_above/below` conditions against session levels; the engine never special-cases it. |
| `rules.RuleSignals` | Evaluates entry conditions on each closed candle. Knows nothing about orders. |
| `trader.Trader` | The event loop: pending orders, fills, protective exits, square-off, max entries, risk limits. Fed one candle at a time (`on_bar`), so it works for batch backtests and streaming paper/live alike. |
| `execution` | `SimulatedFills` (backtest/paper: next-open fills, slippage, tick rounding) and the `OrderGateway` protocol the live executor implements. |
| `charges` | Date-effective statutory/exchange rates with sources, plus configurable brokerage plans. |
| `lots` | Date-effective lot sizes (NSE/FAOP/70616 and others, verification flagged). |
| `calendar` | IST exchange calendar ported from `algora-dash.js`; holidays live in `data/holidays.json`. |
| `backtest` | Wires candles (with resampling), Trader, charges and metrics; adds warnings (holidays, unknown years, unverified rates/lots). |
| `metrics` | Net P&L, win rate, profit factor, max drawdown, per-day stats, trade log. |
| `browser_import` | Converts the static site's export (`cleanBot` rules) into DSL strategies. |

**Fill model (fixes over the browser backtester).** A signal on candle *i*'s close fills at candle *i+1*'s open plus
slippage (never at the signal close). If stop and target are both touched inside one candle, the stop is assumed
first. A candle that opens beyond the stop fills at the open; a candle that opens beyond the target fills the
resting limit at the open. The trailing-stop anchor is updated only after the candle's exit checks, so it never
uses the candle's own extreme to protect a position within that candle. Square-off is capped at the exchange close
of the day (15:30 for index contracts expiring that day from 7 Dec 2026).

### `api/` — FastAPI service
* **Auth:** email one-time code → mandatory TOTP (enrol on first sign-in) → short-lived access JWT (15 min) and an
  opaque refresh token (30 days) stored only as a keyed hash. Refresh rotates the token; presenting an
  already-rotated token revokes the whole family (theft detection). TOTP secrets are Fernet-encrypted at rest and a
  TOTP time step cannot be reused. Email sending is pluggable (`console` in dev, `smtp` otherwise; prod refuses
  `console`).
* **Data:** SQLAlchemy 2 models, Alembic migrations (the test suite builds its schema by running them). Strategy
  rules are immutable `strategy_versions`; every backtest records the spec and version it ran. `audit_log` gets a
  row for every login and create/update/delete, written in the same transaction as the change, and a database
  trigger makes it append-only. Broker accounts are labels only: **no broker API keys or secrets in Phase 1**.
* **Edges:** per-IP and per-user rate limits (Redis in prod), body size limits (413) and `Content-Length`
  required (411), strict request schemas (`extra="forbid"`), CORS for `https://algora25007.github.io`, security headers.
* **Backtests** run synchronously in the request (bounded by 10 MB / 500k candles). Moving them onto a Redis-backed
  worker queue is a Phase 3 item once stored market data replaces uploads.

### `infra/`
docker-compose for local development (TimescaleDB image, so Phase 3 candle hypertables need no new service;
Redis; the API), the API Dockerfile (non-root, migrations on start, health check) and `.env.example`.
Production target per the roadmap: AWS Mumbai (ap-south-1) for data residency.

## Data flow

1. **Sign in** from the static site: `/auth/otp/request` → `/auth/otp/verify` → `/auth/totp/confirm|verify`.
2. **Migrate local data:** the site's Export button produces `{app:"algora",version:1,accounts,bots,backtest}`;
   `POST /import` creates broker-account labels, converts First Candle Cross bots into DSL strategies (other
   templates are reported as not yet available), stores backtest form defaults, and imports the dashboard risk card
   (`{loss,trades,cut}`) if the server has none. Re-importing the same file is idempotent.
3. **Build and version** strategies with `POST/PUT /strategies` (validated against the same schema the site can load
   from `/meta/strategy-schema`).
4. **Backtest:** `POST /backtests` with a CSV and a `strategy_id` (or inline spec) and an optional config
   (slippage, brokerage plan, risk limits). The result (summary, equity curve, per-day stats, trades, warnings,
   charges sources) is stored and listed by `GET /backtests`.

## How paper and live trading plug in

Both reuse `Trader` unchanged; only the candle source and the execution side differ.

**Paper (Phase 3).** A worker per active strategy subscribes to aggregated candles (broker WebSocket or licensed
vendor → TimescaleDB), calls `Trader.on_bar` on each closed candle and records fills from `SimulatedFills`, so
paper results are computed exactly like backtests (same slippage, charges and stop-first rule). Account-wide
`risk_limits` (daily loss, max trades, entry cut-off, kill switch) map onto `trader.RiskLimits`.

**Live (Phase 4) — regulatory constraint.** Under SEBI's retail algo framework (in force from 1 Apr 2026) and NSE
circular **NSE/INSP/77559 (20 Nov 2026)**, which restates NSE/INVG/69255 Annexure I para 14 — *"all the strategies
shall be run on the brokers servers. The order messages shall be originated from brokers server"* — and restricts
static-IP API access to "tech-savvy" clients who code their own algos, a no-code platform like Algora must offer
live trading **as an exchange-empanelled Algo Provider, with strategies hosted on partner-broker servers**.
Therefore:

* The live executor is a separate deployable (`algora_engine` + a thin broker adapter implementing
  `execution.OrderGateway`) packaged to run **inside each partner broker's infrastructure**, not in Algora's cloud.
  The engine already has no network or database dependencies, which is what makes this possible.
* The Algora API stays the control plane: it publishes approved strategy versions (by id + version + exchange algo
  ID) to the broker-hosted executor and receives fills/positions back for the dashboard. Order messages originate
  only from the broker side.
* **Every live order is a limit order** (marketable limit with a configurable protection band in place of market
  orders), tagged with the exchange-issued algo ID, and kept under 10 orders/second (`risk_limits.max_orders_per_second`
  is capped at 10 in the database). The founder's brief states the limit-order requirement; it is not in the
  NSE/INSP/77559 text itself, so confirm the exact clause with the partner broker during empanelment.
* Simulated `market()` fills stay a backtest/paper concept; the live adapter converts each intent into a limit order
  and reconciles asynchronous broker fills back into the Trader's position state.

Until empanelment, public users stay on backtest and paper modes.

## What is deliberately not in Phase 1
* Broker OAuth / API keys (Phase 2, encrypted with KMS), market data ingestion, paper/live workers.
* Options pricing from the underlying: backtests price the instrument whose candles are uploaded (upload option
  premium candles to test options). The browser's "delta" multiplier is not carried over.
* STT on exercised options (v1 strategies square off intraday).
