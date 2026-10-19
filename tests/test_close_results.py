import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_reliability import position
from app.db import Database
from app.alerts import AlertEngine


def fill(**kw):
    return dict(openOrderId=42,closeOrderId=43,pairIndex=1,side='long',
                openTimestamp=10,closeTimestamp=20,closeType='MARKET_CLOSE',
                isPartialClose=False,collateral=100,netPnl=12.5,openPrice=100,closePrice=101.5,
                txHash='0x'+'a'*64) | kw


class Matching(unittest.TestCase):
    def test_confirmed_reasons_and_net_result(self):
        from app.close_results import match_close, format_close_result
        for reason in ['MARKET_CLOSE','TAKE_PROFIT','STOP_LOSS','LIQUIDATION','TWAP_CLOSE','RFQ_CLOSE']:
            result=match_close(position(),[fill(closeType=reason)])
            self.assertIsNotNone(result)
            text=format_close_result(position(symbol='HYPE/USD'),result)
            self.assertIn('+12.50 USDC',text)
            self.assertIn('101.5',text)
            self.assertNotIn('not confirmed',text)

    def test_reused_slot_wrong_side_and_missing_identity_do_not_match(self):
        from app.close_results import match_close
        for row in [fill(openTimestamp=9),fill(side='short'),fill(pairIndex=2),fill(openOrderId=None),fill(openPrice=101)]:
            self.assertIsNone(match_close(position(),[row]))
        self.assertIsNone(match_close(position(opened_at=None),[fill()]))

    def test_ambiguous_same_second_positions_do_not_match(self):
        from app.close_results import match_close
        self.assertIsNone(match_close(position(),[fill(),fill(openOrderId=44,closeOrderId=45)]))

    def test_partial_fills_sum_once_but_do_not_prove_full_close(self):
        from app.close_results import match_close
        partial=fill(closeOrderId=40,closeTimestamp=15,isPartialClose=True,netPnl=-2,collateral=40)
        self.assertIsNone(match_close(position(),[partial]))
        result=match_close(position(),[partial,partial,fill(collateral=60)])
        self.assertEqual(result.net_pnl,10.5)
        self.assertEqual(result.collateral,100)

    def test_zero_pnl_valid_missing_or_nan_pnl_waits(self):
        from app.close_results import match_close
        self.assertEqual(match_close(position(),[fill(netPnl=0)]).net_pnl,0)
        for value in [None,float('nan'),float('inf')]:
            self.assertIsNone(match_close(position(),[fill(netPnl=value)]))


class Reconciliation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'bot.db'
        self.db=Database(self.path);await self.db.connect()
        await self.db.upsert_subscription(1,position().trader)
        self.engine=AlertEngine(self.db,cooldown_seconds=300,hysteresis_percent=.5)
        self.client=SimpleNamespace(fetch_close_history=AsyncMock(return_value=[]))

    async def asyncTearDown(self):
        await self.db.close();self.tmp.cleanup()

    async def disappearance(self):
        await self.engine.evaluate(1,[position(mark_price=None)],False)
        await self.engine.evaluate(1,[],False)

    async def test_close_waits_then_resolves_once_across_restart(self):
        from app.close_results import CloseResultWatcher
        await self.disappearance()
        self.assertEqual(await self.db.pending_alerts(),[])
        await self.db.close();self.db=Database(self.path);await self.db.connect()
        self.client.fetch_close_history.return_value=[fill()]
        watcher=CloseResultWatcher(self.db,self.client)
        await watcher.tick(None);await watcher.tick(None)
        alerts=await self.db.pending_alerts()
        self.assertEqual(len(alerts),1);self.assertIn('+12.50 USDC',alerts[0]['text'])

    async def test_failure_retains_work_and_does_not_block_other_alerts(self):
        from app.close_results import CloseResultWatcher
        await self.disappearance()
        self.client.fetch_close_history.side_effect=RuntimeError('offline')
        await CloseResultWatcher(self.db,self.client).tick(None)
        async with self.db.transaction():
            row=await (await self.db.db.execute('SELECT * FROM pending_closes')).fetchone()
        self.assertIsNotNone(row)
        await self.db.enqueue_alert(1,'Risk alert','liq')
        self.assertEqual(len(await self.db.pending_alerts()),1)

    async def test_mute_stop_switch_cancel_reconciliation(self):
        from app.close_results import CloseResultWatcher
        for action in ['mute','stop','switch']:
            await self.db.upsert_subscription(1,position().trader)
            await self.disappearance()
            if action=='mute':await self.db.set_muted(1,True)
            if action=='stop':await self.db.delete_subscription(1)
            if action=='switch':await self.db.upsert_subscription(1,'0x'+'22'*20)
            self.client.fetch_close_history.return_value=[fill()]
            await CloseResultWatcher(self.db,self.client).tick(None)
            self.assertEqual(await self.db.pending_alerts(),[])

    async def test_reappearing_position_cancels_close(self):
        from app.close_results import CloseResultWatcher
        await self.disappearance()
        await self.engine.evaluate(1,[position(mark_price=None)],False)
        self.client.fetch_close_history.return_value=[fill()]
        await CloseResultWatcher(self.db,self.client).tick(None)
        self.assertEqual(await self.db.pending_alerts(),[])

    async def test_delayed_notice_once_then_confirmed_followup(self):
        from app.close_results import CloseResultWatcher
        await self.disappearance()
        async with self.db.transaction():
            await self.db.db.execute('UPDATE pending_closes SET detected_at=?',(time.time()-120,))
        watcher=CloseResultWatcher(self.db,self.client)
        await watcher.tick(None)
        async with self.db.transaction():
            await self.db.db.execute('UPDATE pending_closes SET next_attempt=0')
        await watcher.tick(None)
        notices=await self.db.pending_alerts()
        self.assertEqual(len(notices),1)
        self.assertEqual(notices[0]['kind'],'closed_pending')
        self.client.fetch_close_history.return_value=[fill(netPnl=-25,closeType='STOP_LOSS')]
        async with self.db.transaction():
            await self.db.db.execute('UPDATE pending_closes SET next_attempt=0')
        await watcher.tick(None)
        notices=await self.db.pending_alerts()
        self.assertEqual(len(notices),2)
        self.assertIn('Stop-loss hit',notices[1]['text'])
        self.assertIn('-25.00 USDC',notices[1]['text'])

    async def test_switch_during_history_fetch_prevents_old_result(self):
        from app.close_results import CloseResultWatcher
        await self.disappearance()
        async def fetch(wallet):
            await self.db.upsert_subscription(1,'0x'+'22'*20)
            return [fill()]
        self.client.fetch_close_history.side_effect=fetch
        await CloseResultWatcher(self.db,self.client).tick(None)
        self.assertEqual(await self.db.pending_alerts(),[])

    async def test_enqueue_failure_keeps_close_for_retry(self):
        from app.close_results import CloseResultWatcher
        from unittest.mock import patch
        await self.disappearance()
        self.client.fetch_close_history.return_value=[fill()]
        watcher=CloseResultWatcher(self.db,self.client)
        with patch.object(self.db,'enqueue_alert',side_effect=RuntimeError('disk full')):
            await watcher.tick(None)
        await watcher.tick(None)
        self.assertEqual(len(await self.db.pending_alerts()),1)
