# Algora

No-code trading strategies for Indian markets (NSE, NFO, MCX): build, backtest, paper trade, then deploy through your own broker.

## Repository layout

| Path | What it is |
| --- | --- |
| `index.html`, `algora-*.js`, `algora-*.css`, `hark.css` | The static front end served by GitHub Pages (from the `gh-pages` branch). Open `index.html` or serve the folder; `.nojekyll` included. |
| `engine/` | `algora_engine`: strategy DSL, event-driven backtester (First Candle Cross), Indian charges, lot sizes, exchange calendar, metrics. |
| `api/` | FastAPI service: email-OTP + TOTP sign-in, strategies, broker-account labels, risk limits, backtests, browser-data import. |
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

## Backtest engine in one paragraph

Strategies are versioned JSON (`engine/schema/strategy.v1.json`). Signals are evaluated on closed candles and fill at
the next candle's open plus configurable slippage; when stop and target are both touched in one candle the stop is
assumed first; gaps through a level fill at the open. Charges (brokerage, STT, exchange, SEBI, stamp duty, GST) are
date-effective with sources in `engine/src/algora_engine/charges.py`; `GET /meta/charges` lists them.

Backtest and paper results are estimates and don't guarantee future returns. Not investment advice.
