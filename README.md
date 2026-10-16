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

## Alert delivery

SQLite stores subscriptions, preferences, position snapshots and a durable outbox. Alert decisions and enqueueing commit together. A separate worker retries failed Telegram sends across restarts. `/mute`, `/stop`, and wallet changes cancel pending messages. Blocked bots pause that chat. Telegram rate limits delay delivery; permanently invalid payloads stay queued with hourly retries for operator investigation.

Delivery is **at least once**: a crash after Telegram accepts a message but before local acknowledgement can cause a duplicate. Urgent warnings are text-only with action buttons. `/positions` and `/test` provide images off the event loop. Closed positions are explicitly unconfirmed; the bot does not infer TP/SL/liquidation fills from prices. Missing fresh prices suppress risk calculations rather than inventing a price.

Default liquidation warnings: net PnL -40/-60/-75% of collateral. TP/SL proximity uses PnL points on a consistent fee basis. Risk computations fall back to gross PnL if SDK net computation is unavailable; verify parity with the exchange before opening access widely.

## Railway deployment

1. Create a service from this repository, root directory `/`, using the Dockerfile. Exactly **one replica** (Telegram long polling and SQLite).
2. Attach a persistent volume at `/data`. Set `SQLITE_PATH=/data/bot.db` and `PORT=8080`.
3. Provision `TELEGRAM_BOT_TOKEN` in Railway Variables. Confirm the Telegram token belongs to the intended bot; change its name/avatar/description through BotFather.
4. Deploy. `/health` reports scheduler liveness, successful reads, delivery progress and queue depth/age; sustained upstream or price failures and delivery backlog return HTTP 503. Monitor this endpoint and logs.
5. Stop the previous bot process before running the same Telegram token here. Retain existing subscribers by migrating their database with the worker stopped.

The Docker deployment deliberately uses Pillow cards and bundled fonts; Node/Chromium candle charts are optional local functionality. This keeps alert delivery independent of browsers or external candle providers.

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
