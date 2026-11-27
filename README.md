# Algora

No-code trading strategies for Indian markets (NSE, NFO, MCX): build, backtest, paper trade, then deploy through your own broker.

## Repository layout

| Path | What it is |
| --- | --- |
| `index.html`, `algora-*.js`, `algora-*.css`, `hark.css` | The static front end served by GitHub Pages (from the `gh-pages` branch). Open `index.html` or serve the folder; `.nojekyll` included. |
| `engine/` | `algora_engine`: strategy DSL (schema v2), event-driven backtester, templates (First Candle Cross, Opening Range Breakout, Gap fade, Momentum), indicators, Indian charges, lot sizes, exchange calendar, metrics. |
| `api/` | FastAPI service: email-OTP + TOTP sign-in, strategies, template catalogue, broker-account labels, risk limits, backtests, browser-data import. |
| `infra/` | docker-compose (Postgres + TimescaleDB, Redis, API), API Dockerfile, `.env.example`. |
| `docs/ARCHITECTURE.md` | Services, data flow, and how paper/live trading plug in (broker-hosted, limit orders). |
| `ACTION_PLAN.md` | Product roadmap. |

## Run it

Requirements: Python 3.12, and either Docker or a local Postgres 16 + Redis 7.

```bash
make setup                      # .venv with engine + api and dev tools
make lint                       # ruff
make test-engine                # engine tests (no services needed)
make test-api TEST_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/algora_test
                                # API tests; the database name must end in _test (it is wiped)
```

### Local stack with Docker

```bash
cp infra/.env.example infra/.env   # fill ALGORA_SECRET_KEY, ALGORA_TOTP_ENCRYPTION_KEY, POSTGRES_PASSWORD
make up                            # API on http://127.0.0.1:8000/docs
make logs                          # dev sign-in codes are printed here (console email sender)
make down
```

### API without Docker

```bash
export $(grep -v '^#' infra/.env | xargs)          # or set the ALGORA_* variables yourself
export ALGORA_DATABASE_URL=postgresql+psycopg://algora:PASS@localhost:5432/algora
make run                                           # alembic upgrade head + uvicorn --reload
```

### Sign-in flow (for the front end)

1. `POST /auth/otp/request {email}` → code emailed (printed to the log in dev).
2. `POST /auth/otp/verify {email, code}` → `{stage, mfa_token}`; on first sign-in `stage=totp_enroll` with
   `totp_secret` and `otpauth_uri` to show as a QR code.
3. `POST /auth/totp/confirm` (first time) or `/auth/totp/verify` with `{mfa_token, code}` → `{access_token, refresh_token}`.
4. Send `Authorization: Bearer <access_token>`; renew with `POST /auth/refresh {refresh_token}`.

Then `POST /import` with the JSON from the site's Export button moves a user's saved accounts, strategies,
risk limits and backtest settings to the server.

## Templates

`GET /templates` lists every template with a description, its parameter JSON schema and defaults, so the front end
can render the form. `POST /templates/{id}/spec {name, instrument, params}` turns form values into a DSL strategy
(nothing is saved); send that spec to `POST /strategies` or `POST /backtests`. Templates only produce DSL rules,
which is what the engine runs.

| Template | Status | What it does (defaults) |
| --- | --- | --- |
| First Candle Cross | Backtest ready | Close beyond the first candle's high/low plus a buffer (15m, buffer 0, reverse on the opposite cross). |
| Opening Range Breakout | Backtest ready | Close beyond the opening range (5m candles, 30-min range, buffer 0, both sides, stop at the other end of the range, target 1x the range width, 1 trade a day). |
| Gap fade | Backtest ready | Fades a 0.5%–3% opening gap against the previous session's close while it is still open (5m, entries until 45 min after the start, 1% stop, target the previous close). |
| Momentum (intraday) | Backtest ready | 9/21 EMA crossover or an N-candle breakout, optional VWAP filter (5m, both sides, reverse or exit on the opposite signal, 1% stop, up to 3 trades a day). |
| Monthly option spread | Not available yet | Needs option-chain data the engine does not have. |

Times left empty use the exchange default (NSE/BSE/NFO/BFO 09:15 start, 15:15 square-off; MCX 09:00 and 23:00).

## Backtest engine in one paragraph

Strategies are versioned JSON (`engine/schema/strategy.v2.json`; v1 documents are upgraded automatically). Signals are evaluated on closed candles and fill at
the next candle's open plus configurable slippage; when stop and target are both touched in one candle the stop is
assumed first; gaps through a level fill at the open. Days the exchange is closed are skipped, and the
square-off is capped at the exchange close (15:30 for index contracts expiring that day from 7 Dec 2026). Charges (brokerage, STT, exchange, SEBI, stamp duty, GST) are
date-effective with sources in `engine/src/algora_engine/charges.py`; `GET /meta/charges` lists them.

Backtest and paper results are estimates and don't guarantee future returns. Not investment advice.
