import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from eth_abi import encode
from app.db import Database
from app.limit_fills import LimitFillWatcher, decode_fill, EVENT_TOPIC, EVENT_TYPES

WALLET='0x'+'11'*20
CALLBACK='0x'+'22'*20

def log(order_type=3, timestamp=2000000000):
    trade=(WALLET,116,0,0,10000000,80000*10**10,True,20*10**10,0,0,timestamp)
    return dict(address=CALLBACK,topics=[EVENT_TOPIC],data='0x'+encode(EVENT_TYPES,
        (123,0,trade,order_type,80001*10**10,10000000,0,0,False,0)).hex(),
        blockNumber=hex(101),transactionHash='0x'+'ab'*32,logIndex='0x0',removed=False)

class FillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/'db');await self.db.connect()
        await self.db.upsert_subscription(1,WALLET)
        self.client=NS(ensure_pairs=AsyncMock(),_pair_symbols={116:'BTC_UPSIDE/USD'})
        self.watcher=LimitFillWatcher(self.db,self.client)
        self.watcher.head=AsyncMock(return_value=106)
        self.watcher.logs=AsyncMock(return_value=[log()])
        self.watcher.block_time=AsyncMock(return_value=2000000000)
    async def asyncTearDown(self):
        await self.watcher.close();await self.db.close();self.tmp.cleanup()
    async def seed(self):
        self.watcher.head.return_value=105
        await self.watcher.tick(None)
        self.watcher.head.return_value=106
    async def test_decode_only_open_fills_uses_execution_price(self):
        fill=decode_fill(log())
        self.assertEqual(fill.price,80001)
        self.assertEqual(fill.wallet,WALLET)
        for kind in [0,1,2,4,5]:self.assertIsNone(decode_fill(log(kind)))
        self.assertIsNone(decode_fill(log() | {'removed':True}))
    async def test_baseline_then_fill_once_across_restart(self):
        await self.seed()
        self.assertEqual(await self.db.pending_alerts(),[])
        await self.watcher.tick(None)
        messages=await self.db.pending_alerts()
        self.assertEqual(len(messages),1)
        self.assertIn('Limit order filled',messages[0]['text'])
        self.assertIn('BTC Upside LONG',messages[0]['text'])
        self.assertIn('80,001',messages[0]['text'])
        await self.watcher.tick(None)
        await self.watcher.close()
        self.watcher=LimitFillWatcher(self.db,self.client)
        self.watcher.head=AsyncMock(return_value=106)
        self.watcher.logs=AsyncMock(return_value=[log()])
        await self.watcher.tick(None)
        self.assertEqual(len(await self.db.pending_alerts()),1)
    async def test_rpc_failure_does_not_advance_cursor(self):
        await self.seed()
        self.watcher.logs.side_effect=RuntimeError('RPC down')
        await self.watcher.tick(None)
        self.watcher.logs.side_effect=None
        await self.watcher.tick(None)
        self.assertEqual(len(await self.db.pending_alerts()),1)
    async def test_muted_fills_are_consumed_without_later_replay(self):
        await self.seed();await self.db.set_muted(1,True)
        await self.watcher.tick(None);await self.db.set_muted(1,False)
        await self.watcher.tick(None)
        self.assertEqual(await self.db.pending_alerts(),[])
    async def test_wrong_wallet_and_pre_subscription_fills_ignored(self):
        await self.seed();await self.db.upsert_subscription(1,'0x'+'33'*20)
        await self.watcher.tick(None)
        self.assertEqual(await self.db.pending_alerts(),[])
    async def test_atomic_failure_rolls_back_cursor_and_outbox(self):
        await self.seed()
        original=self.db.enqueue_alert
        self.db.enqueue_alert=AsyncMock(side_effect=RuntimeError('disk full'))
        await self.watcher.tick(None)
        self.db.enqueue_alert=original
        await self.watcher.tick(None)
        self.assertEqual(len(await self.db.pending_alerts()),1)
    async def test_pre_subscription_event_is_ignored(self):
        await self.seed()
        self.watcher.block_time.return_value=1
        await self.watcher.tick(None)
        self.assertEqual(await self.db.pending_alerts(),[])
    async def test_subscription_switch_resets_start_time(self):
        before=await self.db.get_subscription(1)
        await self.db.upsert_subscription(1,'0x'+'33'*20)
        after=await self.db.get_subscription(1)
        self.assertGreater(after.created_at,before.created_at)
    async def test_short_and_close_events(self):
        await self.seed()
        self.watcher.logs.return_value=[log(0),log(1),log(2),log(4),log(5)]
        await self.watcher.tick(None)
        self.assertEqual(await self.db.pending_alerts(),[])
