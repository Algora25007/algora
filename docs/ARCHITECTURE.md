# Algora architecture (Phase 1 foundation, Phase 2 templates)

Algora lets Indian retail traders build no-code strategies, backtest them, paper trade, and later go live
through their own broker on NSE, NFO and MCX. Phase 1 built the foundation everything else plugs into; Phase 2
adds the Opening Range Breakout, Gap fade and Momentum templates on a more general strategy DSL (schema v2).

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
| `dsl` | Strategy schema v2 (pydantic, `extra="forbid"`). Exported to `engine/schema/strategy.v2.json`; a test fails if the committed schema drifts (`strategy.v1.json` stays as the frozen v1 contract). `load_spec` rejects documents without `schema_version` and upgrades v1 documents (`upgrade`). |
| `templates` | Parameter models (JSON schema + defaults for the front end, `catalogue()`) and builders that *produce DSL*. No template is special-cased in the engine. |
| `indicators` | Streaming EMA, SMA, highest/lowest of the previous N candles, session VWAP. |
| `rules.RuleSignals` | Tracks session state (first candle, opening range, previous session, indicators) and evaluates entry rules and exit conditions on each closed candle. Knows nothing about orders. |
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
of the day (15:30 for index contracts expiring that day from 7 Dec 2026). Days the exchange is closed (weekends,
listed holidays; MCX full holidays, while MCX evening sessions on NSE holidays are traded) are skipped.

### Strategy DSL v2 (Phase 2)

Everything a template needs is a general primitive, so rules compose (an ORB with a VWAP filter, a gap fade with
an EMA condition) without engine changes:

* **Operands:** candle price; constant; `session` levels (`first_candle_*`, `day_open`, `range_open/high/low/mid/close`)
  with `offset_pct` and `offset_points`, and session metrics `range_width_pct` and `gap_pct`; `prev_session`
  open/high/low/close; `indicator` (`ema`, `sma`, `highest`, `lowest` with a length and field, `vwap`), each with
  `continuous` or `session` scope.
* **Conditions:** `gt/gte/lt/lte/crosses_above/crosses_below` between operands, and `time_between` (the signal
  candle's close time, IST). A condition whose operand is unavailable (range still forming, indicator warming up,
  previous session missing) is false.
* **Session:** `opening_range_minutes` (a multiple of the timeframe; default = the first candle) and
  `signals_from` (`after_opening_range`, the v1 behaviour, or `session_start`).
* **Exits:** `stop_pct` or `stop_level` (a level per side, e.g. long at `range_low`, short at `range_high`);
  `target_pct`, `target_level`, `target_r_multiple` (of the initial risk) or `target_range_multiple` (of the
  range width); `trailing_stop_pct`; `flip`; and `conditions` (rule exits, e.g. the opposite crossover). Levels are
  read once when the entry fills. A level already on the wrong side of the fill (possible after a gap) closes the
  trade at that open, stop first.
* **Previous session:** the latest earlier day in the data, accepted only if the exchange calendar (NSE for
  NSE/BSE/NFO/BFO, MCX for MCX) has no trading day in between. In years without a holiday list the check cannot be
  completed; the backtest says so in a warning.
* **Indicators** see every candle of an open day, including candles before the session start and after
  square-off; `crosses_*` never span two days.

v1 → v2 is a strict superset, so `upgrade()` only changes `schema_version`; Alembic migration `0002` does the same
for stored strategy versions (backtest runs keep the spec they ran). Its downgrade refuses if any stored strategy
uses a v2 feature.

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
   `POST /import` creates broker-account labels, converts First Candle Cross, Opening Range Breakout, Gap fade
   and Momentum bots into DSL strategies (Monthly option spread is reported as not available yet), reports per
   strategy which template defaults filled settings the browser did not have (`strategies_created_details`),
   stores backtest form defaults, and imports the dashboard risk card
   (`{loss,trades,cut}`) if the server has none. Re-importing the same file is idempotent.
3. **Pick a template:** `GET /templates` (name, description, parameter schema, defaults) and
   `POST /templates/{id}/spec` to turn form values into a DSL spec.
4. **Build and version** strategies with `POST/PUT /strategies` (validated against the same schema the site can load
   from `/meta/strategy-schema`).
5. **Backtest:** `POST /backtests` with a CSV and a `strategy_id` (or inline spec) and an optional config
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

## Import defaults (browser bots → templates)

The browser stored the same fields for every template (timeframe, entry buffer %, exit mode + %, square-off, max
entries, lots). Settings it did not have use these defaults, and the import response lists them per strategy:

* **Opening Range Breakout:** 30-minute range, buffer kept as %, both sides, reverse on the opposite breakout
  (as the browser did); only the exit the user chose (target % or stop %) is set.
* **Gap fade:** gap 0.5%–3%, both gap directions, only while the gap is still open, entries until 45 minutes after
  the start; the buffer is ignored. Flip has no meaning for a fade, so "flip" bots get a 1% stop and the previous
  close as target; "target"/"stop" bots keep their % and get the other default.
* **Momentum:** 9/21 EMA crossover on the bot's timeframe, no VWAP filter, both sides, reverse and exit on the
  opposite cross; the buffer is ignored. The site described Momentum as a positional rotation across Nifty 50
  names; the engine has intraday momentum only, and the import says so.

## What is deliberately not in Phase 1
* Broker OAuth / API keys (Phase 2, encrypted with KMS), market data ingestion, paper/live workers.
* Options pricing from the underlying: backtests price the instrument whose candles are uploaded (upload option
  premium candles to test options). The browser's "delta" multiplier is not carried over.
* STT on exercised options (strategies square off intraday).
* Monthly option spread (needs option-chain data) and positional / multi-symbol strategies such as a Nifty 50
  momentum rotation.
