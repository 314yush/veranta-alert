"""Wallet reconciliation and durable Telegram delivery, independently scheduled."""
from __future__ import annotations
import asyncio
import logging
import time
from telegram.error import Forbidden, BadRequest, RetryAfter
from .formatting import alert_keyboard, html_send_kwargs

logger = logging.getLogger(__name__)

class Monitor:
    def __init__(self, app, db, client, alerter):
        self.app, self.db, self.client, self.alerter = app, db, client, alerter
        self.started = time.time()
        self.blocked_until = 0.0
        self.last_prices = 0.0
        self.wallet_fetches = {}
        self.wallet_prices = {}
        self.last_poll = 0.0
        self.last_success = 0.0
        self.last_delivery = 0.0
        self.fetch_failures = 0
        self._poll_lock = asyncio.Lock()
        self._delivery_lock = asyncio.Lock()

    async def tick_hot(self, context):
        await self._tick(context, hot=True)

    async def tick_idle(self, context):
        await self._tick(context, hot=False)

    async def _tick(self, context, *, hot):
        async with self._poll_lock:
            subs = await self.db.list_subscriptions(hot=hot)
            for sub in subs:
                await self._process_sub(context, sub)
            self.last_poll = time.time()

    async def _process_sub(self, context, sub):
        try:
            positions = await asyncio.wait_for(self.client.fetch_positions(sub.wallet), 25)
        except Exception:
            self.fetch_failures += 1
            logger.exception("Position fetch failed chat=%s", sub.chat_id)
            return
        # Recheck after network IO: the user may have stopped or switched wallets.
        async with self.db.transaction():
            current = await self.db.get_subscription(sub.chat_id)
            if current is None or current.wallet != sub.wallet:
                return
            await self.db.set_hot(sub.chat_id, bool(positions))
            await self.alerter.evaluate(sub.chat_id, positions, muted=current.muted)
        self.last_success = time.time()
        self.wallet_fetches[(sub.chat_id, sub.wallet)] = self.last_success
        if not positions or all(p.mark_price is not None for p in positions):
            self.last_prices = time.time()
            self.wallet_prices[(sub.chat_id, sub.wallet)] = self.last_prices

    async def deliver_pending(self, context):
        async with self._delivery_lock:
            if time.time() < self.blocked_until:
                return
            for item in await self.db.pending_alerts():
                # Serialize acknowledgement with mute/stop/switch to avoid late sends.
                async with self.db.transaction():
                    pending = await self.db.db.execute("SELECT 1 FROM outbox WHERE id=?", (item['id'],))
                    if await pending.fetchone() is None:
                        continue
                    sub = await self.db.get_subscription(item['chat_id'])
                    if sub is None or sub.muted:
                        await self.db.acknowledge_alert(item['id'])
                        continue
                    try:
                        # Text goes first. Chart failures never hold up a risk warning.
                        await context.bot.send_message(
                            chat_id=item['chat_id'], text=(f"<i>Delayed alert from {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(item['created_at']))}</i>\n" if time.time()-item['created_at'] > 60 else '') + item['text'],
                            **html_send_kwargs(reply_markup=alert_keyboard()),
                        )
                    except Forbidden:
                        await self.db.set_muted(item['chat_id'], True)
                        logger.warning("Bot blocked; paused chat=%s", item['chat_id'])
                    except BadRequest:
                        # Keep the message for inspection/retry rather than silently lose it.
                        await self.db.retry_alert(item['id'], 3600)
                        logger.exception("Telegram rejected queued alert id=%s", item['id'])
                    except RetryAfter as exc:
                        delay = exc.retry_after
                        if hasattr(delay, 'total_seconds'):
                            delay = delay.total_seconds()
                        self.blocked_until = time.time() + float(delay) + 1
                        await self.db.retry_alert(item['id'], float(delay)+1)
                        return
                    except Exception:
                        await self.db.retry_alert(item['id'], min(300, 2**min(item['attempts']+1, 8)))
                        logger.warning("Delivery delayed id=%s", item['id'])
                    else:
                        await self.db.acknowledge_alert(item['id'])
                        self.last_delivery = time.time()
                await asyncio.sleep(1.05)

    async def health_snapshot(self):
        now = time.time()
        async with self.db.transaction():
            cursor = await self.db.db.execute("SELECT COUNT(*),MIN(created_at) FROM outbox")
            depth, oldest = await cursor.fetchone()
            subs = await self.db.list_subscriptions()
        reasons = []
        if now - (self.last_poll or self.started) > 300:
            reasons.append("polling_stalled")
        keys = {(sub.chat_id, sub.wallet) for sub in subs}
        self.wallet_fetches = {k: v for k, v in self.wallet_fetches.items() if k in keys}
        self.wallet_prices = {k: v for k, v in self.wallet_prices.items() if k in keys}
        for sub in subs:
            key = (sub.chat_id, sub.wallet)
            baseline = max(self.started, sub.created_at)
            if now - self.wallet_fetches.get(key, baseline) > 360:
                reasons.append("upstream_stale")
            if sub.is_hot and now - self.wallet_prices.get(key, baseline) > 180:
                reasons.append("prices_stale")
        reasons = sorted(set(reasons))
        oldest_age = now-oldest if oldest else 0
        if oldest_age > 300:
            reasons.append("delivery_backlog")
        return dict(status="degraded" if reasons else "ok", reasons=reasons,
                    last_poll=self.last_poll, last_successful_fetch=self.last_success,
                    last_delivery=self.last_delivery, fetch_failures=self.fetch_failures,
                    queue_depth=depth, oldest_queued_seconds=oldest_age)
