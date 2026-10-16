# Veranta Alerts

Watch-only Telegram alerts for Veranta positions on Base. Users send a public wallet address; the bot never trades. Python worker at repository root; React/Vite landing page in `web/`.

## Run the bot

Use Python 3.13. Create a virtual environment, install `requirements.txt`, and copy `.env.example` to `.env` **only if it does not already exist**. Configure `TELEGRAM_BOT_TOKEN`. Public position and price reads require no signing key. Optional legacy `AVANTIS_*` and `VERANTA_*` operator settings remain accepted. Never commit secrets.

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bot.py
```

The bot accepts private chats. Commands: `/start`, `/help`, `/positions`, `/test`, `/settings`, `/mute`, `/unmute`, `/stop`. One wallet per chat. Replacing a wallet preserves preferences but clears old snapshots and pending notifications.

Open wallets poll every 15 seconds; idle wallets every 180 seconds. A newly opened position may therefore take up to about three minutes to be discovered, plus upstream latency. Polling is best effort and not a liquidation guarantee.

## Limit-order fill notifications

Confirmed limit opens send one short Telegram message with pair, direction and execution entry price. A separate Base event scanner runs every 15 seconds and waits five blocks; normal latency is roughly 10–30 seconds plus provider/delivery latency, including for wallets with no open positions. It recognizes the deployed `LimitExecuted` OPEN event, not disappeared orders. Cancellations, TP/SL executions and liquidations do not produce fill messages.

The durable block cursor and queued messages commit together, so restart/retry does not replay scanned events. First installation establishes a current-block baseline and does not announce old trades. Mute/stop applies; muted fills are consumed silently. The existing delivery queue is at least once. Set `LIMIT_FILLS_RPC_URL` for a dedicated Base RPC; the default is PublicNode. Sustained scanner failures degrade `/health` with `limit_fills_stale`.

## Alert delivery

SQLite stores subscriptions, preferences, position snapshots and a durable outbox. Alert decisions and enqueueing commit together. A separate worker retries failed Telegram sends across restarts. `/mute`, `/stop`, and wallet changes cancel pending messages. Blocked bots pause that chat. Telegram rate limits delay delivery; permanently invalid payloads stay queued with hourly retries for operator investigation.

Delivery is **at least once**: a crash after Telegram accepts a message but before local acknowledgement can cause a duplicate. Urgent warnings are text-only with action buttons. `/positions` provides one compact summary, with pagination above eight positions. Tap a position’s Chart button for an image; `/test` also provides a preview off the event loop. Closed positions are explicitly unconfirmed; the bot does not infer TP/SL/liquidation fills from prices. Missing fresh prices suppress risk calculations rather than inventing a price.

Default liquidation warnings: 45/25/10 collateral points remaining to the actual liquidation price. TP/SL proximity uses leveraged price distance. Net PnL uses SDK fees and funding; missing net computations are explicitly unavailable. See [calculation audit](docs/PNL.md).

## Railway deployment

1. Create a service from this repository, root directory `/`, using the Dockerfile. Exactly **one replica** (Telegram long polling and SQLite).
2. Attach a persistent volume at `/data`. Set `SQLITE_PATH=/data/bot.db` and `PORT=8080`.
3. Provision `TELEGRAM_BOT_TOKEN` in Railway Variables. Confirm the Telegram token belongs to the intended bot; change its name/avatar/description through BotFather.
4. Deploy. `/health` reports scheduler liveness, successful reads, delivery progress and queue depth/age; sustained upstream or price failures and delivery backlog return HTTP 503. Monitor this endpoint and logs.
5. Stop the previous bot process before running the same Telegram token here. Retain existing subscribers by migrating their database with the worker stopped.

The Docker deployment uses native Pillow candle charts and bundled fonts without Node/Chromium. Charts show entry, TP, SL and liquidation, with labelled reference-candle and observed-tick fallbacks. Alert delivery remains independent of chart providers.

### Backup and restore

```sh
python scripts/backup_db.py /data/bot.db /data/backups/bot-YYYY-MM-DD.db
```

Schedule daily snapshots and copy them off the service volume (or enable the host's volume backup feature). The script refuses to overwrite an existing destination and verifies SQLite integrity. To restore: stop the worker, retain the current database as a rollback copy, replace `bot.db` with a verified backup, remove stale `bot.db-wal`/`bot.db-shm` only while stopped, then start one worker. Tick history is optional; subscriptions and the outbox are in `bot.db`.

## Landing page / Vercel

Set Vercel project root to `web/`. Node 22.12+; build `npm run build`; output `dist`. Configure `VITE_TELEGRAM_BOT_URL` to the actual bot URL. The default is `https://t.me/veranta_alertsbot`. No Telegram token or SDK secret belongs in frontend environment variables.

```sh
cd web
npm ci
npm run dev
```

## Verification

```sh
python -m unittest discover -s tests -v
python scripts/smoke_alert_prefs.py
python scripts/smoke_classify_exit.py
python -c 'import app.bot'
```

GitHub Actions runs offline tests, frontend build and Docker build. The live SDK smoke (`scripts/smoke_sdk_positions.py <wallet>`) uses public reads; no signing credentials are required. Launch acceptance: fresh user starts, subscribes, sees correct live positions, receives a controlled warning, survives worker restart and temporary delivery failure, then mutes/unmutes and stops successfully. Do not open a risky trade solely to test alerts.

## Chart rendering

Position cards use the same 1080×720 renderer as alert cards, with tightly spaced 5-minute candles and a two-hour window. The price scale uses observed highs/lows plus 6% padding; distant risk levels stay in the stats grid. Changed subsecond observations are retained before OHLC aggregation, gaps stay empty, and isolated observations appear as dots. Recent Veranta observations are preferred; official five-minute candles and explicitly labeled spot references are fallbacks.

`python scripts/preview_trade_durations.py` generates simulated 2m/5m/10m/10h examples using 5s/15s/30s/30m candles. These are renderer previews, not an automatic runtime timeframe selector. Local tick retention remains two hours.
