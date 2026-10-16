"""Confirmed limit-open events, with an atomic durable scan cursor and outbox.

ABI: Base callback module 0x1b21d824a17edd5f068a304fcf10aed9e3bd1747,
verified 2026-10-16. Events are emitted at the callback proxy via delegatecall.
OPEN=3 is verified against the official SDK /v2/meta limitOrderType enum.
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from html import escape

import httpx
from eth_abi import decode
from eth_utils import keccak
from .formatting import display_pair

logger = logging.getLogger(__name__)
TRADE_TYPE = '(address,uint256,uint256,uint256,uint256,uint256,bool,uint256,uint256,uint256,uint256)'
EVENT_TYPES = ['uint256', 'uint256', TRADE_TYPE, 'uint8', 'uint256', 'uint256', 'int256', 'uint256', 'bool', 'uint256']
EVENT_TOPIC = '0x' + keccak(text='LimitExecuted('+','.join(EVENT_TYPES)+')').hex()

@dataclass
class Fill:
    wallet: str
    pair: int
    is_long: bool
    price: float


def decode_fill(row):
    if row.get('removed') or row.get('topics') != [EVENT_TOPIC]:
        return None
    values = decode(EVENT_TYPES, bytes.fromhex(row['data'].removeprefix('0x')))
    if values[3] != 3:  # Exclude TP, SL, liquidation, and partial closes.
        return None
    trade = values[2]
    return Fill(trade[0].lower(), int(trade[1]), bool(trade[6]), values[4]/10**10)


class LimitFillWatcher:
    def __init__(self, db, client):
        self.db, self.client = db, client
        self.http = httpx.AsyncClient(timeout=15)
        self.rpc_url = os.getenv('LIMIT_FILLS_RPC_URL', 'https://base-rpc.publicnode.com')
        self.started = time.time()
        self.last_success = 0.0
        self._lock = asyncio.Lock()
        self._callback = None
        self._callback_at = 0.0

    async def close(self):
        await self.http.aclose()

    async def rpc(self, method, params):
        r = await self.http.post(self.rpc_url, json=dict(jsonrpc='2.0',id=1,method=method,params=params))
        r.raise_for_status()
        payload = r.json()
        if 'error' in payload or 'result' not in payload:
            raise RuntimeError('Limit fill RPC request failed: '+method)
        return payload['result']

    async def head(self):
        if int(await self.rpc('eth_chainId', []),16) != 8453:
            raise RuntimeError('Limit fill RPC must be Base mainnet')
        return int(await self.rpc('eth_blockNumber', []),16)

    async def callback_address(self):
        if self._callback is None or time.time()-self._callback_at > 300:
            meta = await self.client.client.meta()
            if meta['chainId'] != 8453 or meta['enums']['limitOrderType']['open'] != 3:
                raise RuntimeError('Unsupported limit-fill deployment')
            result = await self.rpc('eth_call', [dict(to=meta['addresses']['tradingStorage'],
                data='0x'+keccak(text='callbacks()')[:4].hex()),'latest'])
            if len(result) != 66 or int(result,16) == 0:
                raise RuntimeError('Invalid callback address')
            self._callback = '0x'+result[-40:]
            self._callback_at = time.time()
        return self._callback

    async def logs(self, start, end):
        return await self.rpc('eth_getLogs', [dict(address=await self.callback_address(),
            fromBlock=hex(start),toBlock=hex(end),topics=[EVENT_TOPIC])])

    async def block_time(self, number):
        block = await self.rpc('eth_getBlockByNumber',[hex(number),False])
        return int(block['timestamp'],16)

    async def tick(self, context):
        async with self._lock:
            try:
                await self._scan()
            except Exception:
                # A failed read or transaction never advances the durable cursor.
                logger.exception('Limit fill scan failed; will retry')

    async def _scan(self):
        tip = max(0, await self.head()-5)  # Allow five Base blocks before notification.
        async with self.db.transaction():
            await self.db.db.execute('CREATE TABLE IF NOT EXISTS limit_fill_cursor (id INTEGER PRIMARY KEY, block INTEGER NOT NULL)')
            cur = await self.db.db.execute('SELECT block FROM limit_fill_cursor WHERE id=1')
            row = await cur.fetchone()
            if row is None:
                await self.db.db.execute('INSERT INTO limit_fill_cursor VALUES (1,?)',(tip,))
                self.last_success = time.time()
                return  # Deployment baseline: never announce historical trades.
            cursor = row['block']
        # Bounded batches let this job catch up across restarts without monopolizing IO.
        for _ in range(8):
            if cursor >= tip:
                self.last_success = time.time()
                return
            end = min(cursor+500,tip)
            rows = await self.logs(cursor+1,end)
            fills = []
            times = {}
            for row in rows:
                fill = decode_fill(row)
                if fill is None:
                    continue
                number = int(row['blockNumber'],16)
                if not cursor < number <= end:
                    raise RuntimeError('Out-of-range execution event')
                if number not in times:
                    times[number] = await self.block_time(number)
                fills.append((fill,times[number]))
            if fills:
                await self.client.ensure_pairs()
            async with self.db.transaction():
                # Resolve subscribers at commit time to respect mute/stop/wallet changes.
                subscribers = await self.db.list_subscriptions()
                for fill, timestamp in fills:
                    for sub in subscribers:
                        if sub.muted or sub.wallet.lower() != fill.wallet or timestamp+1 < sub.created_at:
                            continue
                        symbol = self.client._pair_symbols.get(fill.pair, f'Pair {fill.pair}')
                        price = f'{fill.price:,.2f}' if fill.price >= 10 else f'{fill.price:.8f}'.rstrip('0').rstrip('.')
                        text = (f'✅ <b>Limit order filled</b>\n'
                                f'{escape(display_pair(symbol))} {"LONG" if fill.is_long else "SHORT"} · Entry ${price}')
                        await self.db.enqueue_alert(sub.chat_id,text,'limit_filled')
                        await self.db.set_hot(sub.chat_id,True)
                await self.db.db.execute('UPDATE limit_fill_cursor SET block=? WHERE id=1',(end,))
            cursor = end
        if cursor >= tip:
            self.last_success = time.time()
