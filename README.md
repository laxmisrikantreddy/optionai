# OptionAI — Live Option-Buy Signal Generator

Pulls live NSE option-chain data from your **Dhan** account, runs rule-based
checks each poll, and surfaces option-buy signals (with stop-loss and target)
on a live FastAPI + HTML dashboard.

## What it does

- Polls Dhan v2 option-chain endpoints for configured underlyings (default: NIFTY, BANKNIFTY)
- Filters strikes by a delta band (default 0.35–0.55) so only ATM-ish, decent-gamma strikes qualify
- Emits buy signals when rules trigger:
  - `LONG_BUILDUP` — OI ↑ + LTP ↑ on a CE/PE
  - `SHORT_COVERING` — OI ↓ + LTP ↑ on a CE/PE
- Computes **stop-loss** and **target** for every signal (default: 30% SL, 1:2 RR — both configurable)
- Streams signals to a live dashboard at `/`, with auto-refresh every 3s

## Setup

Requires Python 3.11+.

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env and put your Dhan creds
```

Get your Dhan credentials from: web.dhan.co → My Profile → DhanHQ Trading APIs →
generate an access token. You'll need both `client_id` and `access_token`.

(Optional) edit `config.yaml` to change underlyings, poll interval, SL/target,
or rule thresholds.

## Run

```bash
uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

## Endpoints

| Path                | What it returns                                        |
|---------------------|--------------------------------------------------------|
| `GET /`             | Dashboard (HTML)                                       |
| `GET /api/signals`  | Recent signals, newest first (`?limit=` to cap)        |
| `GET /api/health`   | Worker status, last poll time, last error              |
| `GET /api/config`   | Current rules / risk / underlyings configuration       |

## Configuration (`config.yaml`)

```yaml
underlyings:
  - { name: NIFTY,     scrip: 13, segment: IDX_I }
  - { name: BANKNIFTY, scrip: 25, segment: IDX_I }

poll_interval_seconds: 5

risk:
  sl_pct: 0.30        # 30% of premium = SL
  rr: 2.0             # target = entry * (1 + sl_pct * rr) -> 60% above entry

rules:
  delta_min: 0.35
  delta_max: 0.55
  oi_change_pct_min: 20.0
  price_change_pct_min: 5.0
  short_cover_oi_drop_pct: 15.0
```

## Notes & gotchas

- **Dhan rate limits**: option-chain endpoint is ~1 req / 3s. With the default
  poll interval (5s) and 2 underlyings, each cycle issues 4 calls (expiry +
  chain × 2) and is comfortably within limits. If you add more underlyings,
  raise `poll_interval_seconds`.
- **Market hours only**: signals are only meaningful during NSE hours
  (09:15–15:30 IST). Outside hours the chain is stale and most rules will
  return nothing.
- **Not financial advice.** This is a signal-discovery / research tool. You
  place orders yourself; trade at your own risk.

## Roadmap

- Pluggable provider interface (NSE direct / Kite / Angel) behind `DataProvider`
- More rules: PCR shift, max-pain drift, IV percentile filter
- Persistence (SQLite) so signals survive restarts
- Telegram / WhatsApp alerts
- Optional auto-square-off / order placement via Dhan trading API
