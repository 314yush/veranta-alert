import asyncio
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from telegram.error import TimedOut
from app.db import Database
from app.alerts import AlertEngine, classify_exit
from app.veranta_sdk import Position
from app.monitor import Monitor


def position(**kw):
    args = dict(trader='0x'+'11'*20, pair_index=1,index=0,is_long=True,
        collateral_usdc=100,leverage=10,open_price=100,tp=0,sl=0,
        liquidation_price=91.5,mark_price=95,unrealized_pnl_pct=-50,opened_at=10)
    return Position(**(args | kw))

class Reliability(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.db'
        self.db=Database(self.path); await self.db.connect()
        await self.db.upsert_subscription(1,position().trader)
        self.engine=AlertEngine(self.db,cooldown_seconds=300,hysteresis_percent=.5)
    async def asyncTearDown(self):
        await self.db.close(); self.tmp.cleanup()
    async def test_wallet_change_does_not_report_old_positions_closed(self):
        await self.engine.evaluate(1,[position()],False)
        await self.db.upsert_subscription(1,'0x'+'22'*20)
        self.assertEqual(await self.engine.evaluate(1,[],False),[])
    async def test_new_lifecycle_gets_its_own_warning(self):
        await self.engine.evaluate(1,[position()],False)
        alerts=await self.engine.evaluate(1,[position(opened_at=20)],False)
        self.assertIn('pnl_-40',[a.kind for a in alerts])
    async def test_close_reason_is_not_guessed_from_price(self):
        self.assertEqual(classify_exit(position(mark_price=80),80),'closed')
    async def test_pending_delivery_survives_restart(self):
        await self.engine.evaluate(1,[position()],False)
        await self.db.close(); self.db=Database(self.path); await self.db.connect()
        self.assertEqual(len(await self.db.pending_alerts()),1)
    async def test_failure_keeps_message_pending(self):
        await self.engine.evaluate(1,[position()],False)
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimedOut()))
        monitor=Monitor(None,self.db,None,self.engine)
        await monitor.deliver_pending(SimpleNamespace(bot=bot))
        self.assertEqual(len(await self.db.pending_alerts(include_delayed=True)),1)
    async def test_success_acknowledges_delivery(self):
        await self.engine.evaluate(1,[position()],False)
        bot=SimpleNamespace(send_message=AsyncMock())
        monitor=Monitor(None,self.db,None,self.engine)
        await monitor.deliver_pending(SimpleNamespace(bot=bot))
        self.assertEqual(await self.db.pending_alerts(include_delayed=True),[])
    async def test_mute_cancels_pending_alerts(self):
        await self.engine.evaluate(1,[position()],False)
        await self.db.set_muted(1,True)
        self.assertEqual(await self.db.pending_alerts(include_delayed=True),[])
    async def test_stop_cancels_pending_alerts(self):
        await self.engine.evaluate(1,[position()],False)
        await self.db.delete_subscription(1)
        self.assertEqual(await self.db.pending_alerts(include_delayed=True),[])
    async def test_switch_cancels_pending_alerts(self):
        await self.engine.evaluate(1,[position()],False)
        await self.db.upsert_subscription(1,'0x'+'22'*20)
        self.assertEqual(await self.db.pending_alerts(include_delayed=True),[])
    async def test_missing_mark_does_not_trigger_risk_alert(self):
        self.assertEqual(await self.engine.evaluate(1,[position(mark_price=None,unrealized_pnl_pct=None)],False),[])
    async def test_evaluation_and_queue_rollback_together(self):
        with self.assertRaises(RuntimeError):
            async with self.db.transaction():
                await self.engine.evaluate(1,[position()],False)
                raise RuntimeError('crash')
        self.assertEqual(await self.db.list_position_snapshots(1),{})
        self.assertEqual(await self.db.pending_alerts(include_delayed=True),[])
        self.assertEqual(len(await self.engine.evaluate(1,[position()],False)),1)
    async def test_retry_eventually_delivers_without_regenerating_warning(self):
        await self.engine.evaluate(1,[position()],False)
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimedOut()))
        monitor=Monitor(None,self.db,None,self.engine)
        await monitor.deliver_pending(SimpleNamespace(bot=bot))
        queued=await self.db.pending_alerts(include_delayed=True)
        await self.db.retry_alert(queued[0]['id'],0)
        bot.send_message=AsyncMock()
        await monitor.deliver_pending(SimpleNamespace(bot=bot))
        self.assertEqual(await self.db.pending_alerts(include_delayed=True),[])
        self.assertEqual(await self.engine.evaluate(1,[position()],False),[])
    async def test_fetch_finishing_after_wallet_change_is_discarded(self):
        sub=await self.db.get_subscription(1)
        async def fetch(wallet):
            await self.db.upsert_subscription(1,'0x'+'22'*20)
            return [position()]
        monitor=Monitor(None,self.db,SimpleNamespace(fetch_positions=fetch),self.engine)
        await monitor._process_sub(None,sub)
        self.assertEqual(await self.db.list_position_snapshots(1),{})
    async def test_legacy_environment_still_loads(self):
        from unittest.mock import patch
        from app.config import load_settings
        with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'test','AVANTIS_PRIVATE_KEY':'legacy','AVANTIS_TRADER_ADDRESS':'wallet'},clear=True):
            self.assertEqual(load_settings().veranta_private_key,'legacy')
    async def test_tp_at_mark_is_not_missed_due_to_fees(self):
        p=position(mark_price=101,tp=101,unrealized_pnl_pct=-10,gross_pnl_usdc=10)
        await self.db.save_alert_prefs((await self.db.get_alert_prefs(1)).with_updates(tp_distance_pnl=5))
        alerts=await self.engine.evaluate(1,[p],False)
        self.assertIn('tp_prox',[a.kind for a in alerts])
    async def test_rate_limit_pauses_other_queued_messages(self):
        from telegram.error import RetryAfter
        await self.engine.evaluate(1,[position(unrealized_pnl_pct=-65)],False)
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=RetryAfter(60)))
        monitor=Monitor(None,self.db,None,self.engine)
        await monitor.deliver_pending(SimpleNamespace(bot=bot))
        await monitor.deliver_pending(SimpleNamespace(bot=bot))
        rows=await self.db.pending_alerts(include_delayed=True)
        self.assertEqual(sum(r['attempts'] for r in rows),1)
    async def test_health_detects_stale_queue(self):
        import time
        await self.engine.evaluate(1,[position()],False)
        async with self.db.transaction():
            await self.db.db.execute('UPDATE outbox SET created_at=?',(time.time()-600,))
        monitor=Monitor(None,self.db,None,self.engine)
        monitor.last_poll=monitor.last_success=time.time()
        status=await monitor.health_snapshot()
        self.assertEqual(status['status'],'degraded')
        self.assertEqual(status['queue_depth'],1)
    async def test_health_detects_upstream_failure_despite_poll_heartbeat(self):
        import time
        monitor=Monitor(None,self.db,None,self.engine)
        monitor.started=time.time()-600
        monitor.last_poll=time.time()
        monitor.last_success=time.time()-500
        async with self.db.transaction():
            await self.db.db.execute("UPDATE subscriptions SET created_at=?", (time.time()-600,))
        self.assertEqual((await monitor.health_snapshot())['status'],'degraded')
    async def test_healthy_wallet_does_not_hide_stale_active_wallet(self):
        import time
        await self.db.set_hot(1,True)
        await self.db.upsert_subscription(2,'0x'+'22'*20)
        async with self.db.transaction():
            await self.db.db.execute('UPDATE subscriptions SET created_at=?',(time.time()-600,))
        monitor=Monitor(None,self.db,None,self.engine)
        monitor.started=time.time()-600
        monitor.last_poll=monitor.last_success=monitor.last_prices=time.time()
        self.assertEqual((await monitor.health_snapshot())['status'],'degraded')
    async def test_watch_only_startup_needs_no_trading_key(self):
        from unittest.mock import patch
        from app.config import load_settings
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN':'test'}, clear=True):
            settings=load_settings()
            self.assertIsNone(settings.veranta_private_key)
            self.assertIsNone(settings.veranta_trader_address)
