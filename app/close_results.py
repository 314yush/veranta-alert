"""Durable close-result reconciliation using confirmed, paired execution history.

Schema: https://api.veranta.xyz/docs/ (intelligence/trades).
The endpoint returns every close fill, links it to its originating open order,
and caches results for five minutes. Never infer a cause from a last mark.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from html import escape

from .alerts import position_from_json
from .formatting import display_pair, format_exit_alert

logger = logging.getLogger(__name__)
REASONS = {'MARKET_CLOSE': 'Market close', 'TAKE_PROFIT': 'Take-profit hit',
           'STOP_LOSS': 'Stop-loss hit', 'LIQUIDATION': 'Liquidated',
           'TWAP_CLOSE': 'TWAP close', 'RFQ_CLOSE': 'RFQ close'}


@dataclass(frozen=True)
class CloseResult:
    reason: str
    net_pnl: float
    collateral: float
    price: float
    timestamp: int
    tx_hash: str | None
    fills: int


def match_close(pos, rows):
    """Exact lifecycle matching, with no symbol/price-only fallback."""
    if not pos.opened_at:
        return None
    matched = [r for r in rows if isinstance(r, dict)
               and r.get('openTimestamp') == pos.opened_at
               and r.get('pairIndex') == pos.pair_index
               and r.get('side') == pos.side.lower()]
    if any(type(r.get('openOrderId')) is not int or type(r.get('closeOrderId')) is not int for r in matched):
        return None
    groups = {r.get('openOrderId') for r in matched}
    if len(groups) != 1 or None in groups:
        return None  # Same-second positions cannot safely be distinguished.
    unique = {}
    for row in matched:
        key = row.get('closeOrderId')
        if key is None or (key in unique and unique[key] != row):
            return None
        unique[key] = row
    fills = list(unique.values())
    try:
        for row in fills:
            if (not all(math.isfinite(float(row[k])) for k in ('netPnl','collateral','closePrice','closeTimestamp'))
                    or float(row['collateral']) <= 0 or float(row['closePrice']) <= 0
                    or row['closeTimestamp'] < pos.opened_at or row['closeTimestamp'] > time.time()+5):
                return None
        last = max(fills, key=lambda r: r['closeTimestamp'])
        # Prior partial closes alone must never masquerade as a final settlement.
        if last.get('isPartialClose') is not False:
            return None
        if not math.isclose(float(last['openPrice']), pos.open_price, rel_tol=1e-8, abs_tol=1e-9):
            return None
        reason = REASONS.get(last.get('closeType'), 'Closed (reason unavailable)')
        return CloseResult(reason, sum(float(r['netPnl']) for r in fills),
                           sum(float(r['collateral']) for r in fills),
                           float(last['closePrice']), int(last['closeTimestamp']),
                           last.get('txHash'), len(fills))
    except (ValueError, TypeError, KeyError):
        return None


def format_close_result(pos, result):
    pct = result.net_pnl/result.collateral*100
    name = escape(display_pair(pos.symbol))
    text = (f'<b>{name} {pos.side} · {result.reason}</b>\n'
            f'Realized PnL: <b>{result.net_pnl:+,.2f} USDC ({pct:+.1f}%)</b>\n'
            f'Final close price: {result.price:,.6f}'.rstrip('0').rstrip('.'))
    if result.fills > 1:
        text += f'\nIncludes {result.fills} close fills.'
    text += '\n'+time.strftime('%d %b %Y · %H:%M UTC', time.gmtime(result.timestamp))
    if isinstance(result.tx_hash, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', result.tx_hash):
        text += f' · <a href="https://basescan.org/tx/{result.tx_hash}">Transaction</a>'
    return text


class CloseResultWatcher:
    def __init__(self, db, client):
        self.db, self.client = db, client
        self._lock = asyncio.Lock()

    async def tick(self, context):
        async with self._lock:
            try:
                await self._tick()
            except Exception:
                logger.exception('Close-result reconciliation failed; work retained')

    async def _tick(self):
        now = time.time()
        async with self.db.transaction():
            cursor = await self.db.db.execute(
                'SELECT * FROM pending_closes WHERE next_attempt<=? ORDER BY next_attempt LIMIT 100', (now,))
            pending = [dict(row) for row in await cursor.fetchall()]
        histories = {}
        for item in pending:
            wallet = item['wallet']
            if wallet not in histories:
                try:
                    histories[wallet] = await asyncio.wait_for(self.client.fetch_close_history(wallet), 15)
                except Exception:
                    histories[wallet] = []
                    logger.warning('Close history unavailable; scheduled retry')
            pos = position_from_json(item['payload_json'])
            result = match_close(pos, histories[wallet])
            async with self.db.transaction():
                current = await (await self.db.db.execute(
                    'SELECT detected_at FROM pending_closes WHERE chat_id=? AND position_key=?',
                    (item['chat_id'], item['position_key']))).fetchone()
                if current is None or current['detected_at'] != item['detected_at']:
                    continue
                sub = await self.db.get_subscription(item['chat_id'])
                if sub is None or sub.muted or sub.wallet != wallet:
                    await self._delete(item)
                    continue
                if result:
                    await self.db.enqueue_alert(item['chat_id'],format_close_result(pos,result),'closed')
                    await self._delete(item)
                else:
                    # Brief notice only if indexing is slow; the eventual summary is durable.
                    notified = item['notified']
                    if not notified and now-item['detected_at']>=60:
                        await self.db.enqueue_alert(item['chat_id'],format_exit_alert('closed',pos,None),'closed_pending')
                        notified = 1
                    delay = min(300, 15*2**min(item['attempts'],5))
                    await self.db.db.execute(
                        'UPDATE pending_closes SET next_attempt=?,attempts=attempts+1,notified=? WHERE chat_id=? AND position_key=?',
                        (now+delay,notified,item['chat_id'],item['position_key']))

    async def _delete(self, item):
        await self.db.db.execute('DELETE FROM pending_closes WHERE chat_id=? AND position_key=?',
                                 (item['chat_id'],item['position_key']))
