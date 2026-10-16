# Veranta Alerts launch plan

Approved scope: harden the existing watch-only bot, migrate to Veranta, deploy bot and landing page, and verify real delivery. Preserve public-address onboarding; no user signing keys or trading.

1. Regression tests for delivery failures/restarts, wallet replacement, position lifecycle, mute/stop, stale data and conservative close labels.
2. SQLite transactional outbox: persist alert state and pending text together; retry transient Telegram failures; remove permanent failures; serialize database transactions. At-least-once delivery, with possible duplicates if the process dies after Telegram accepts a message but before acknowledgement commits.
3. Deliver alert text independently of chart rendering. Run on-demand image work in a thread. Reconcile positions on each cadence; expose heartbeat and queue health. Use explicit unknown-close wording instead of guessed executions.
4. Migrate Python imports/env names with legacy env fallback. Pin dependencies. Configure the web bot URL through deployment settings, without inventing a new username.
5. Single worker Docker/Railway deployment with a persistent /data volume, SQLite backup utility and documented restore steps. Vercel serves web/. CI runs offline tests and web build.
6. Publish to the supplied GitHub repository after tests and review. Live deployment and Telegram smoke require the real host configuration and secret provisioning.

Verification: unittest discovery, existing prefs smoke, imports, frontend build, container configuration check; live read and delivery only when configured. Review focus: restart after failed send, mute with queued messages, wallet replacement, reused position IDs, stale upstream data. Never include runtime data or secrets in Git.
