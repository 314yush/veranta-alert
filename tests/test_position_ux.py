import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as NS
from dataclasses import replace
from test_reliability import position
from app.formatting import format_positions, format_exit_alert
from app.handlers import _deliver_positions, PositionsLoad
from app.veranta_sdk import VerantaSDK, _pnl_for_sdk_position

class PositionUX(unittest.IsolatedAsyncioTestCase):
    async def test_positions_is_one_text_message(self):
        positions=[position(symbol='BTC/USD'),position(symbol='ETH/USD',pair_index=2)]
        bot=NS(send_message=AsyncMock(),send_photo=AsyncMock())
        load=PositionsLoad(format_positions(positions,'0x'+'11'*20),positions,False)
        await _deliver_positions(NS(bot=bot),1,load)
        self.assertEqual(bot.send_message.call_count,1)
        self.assertEqual(bot.send_photo.call_count,0)
        self.assertLess(len(bot.send_message.call_args.kwargs['text']),800)
    async def test_empty_portfolio_explains_monitoring_continues(self):
        self.assertIn('still watching',format_positions([],'0x'+'11'*20).lower())
    async def test_closed_notification_does_not_dump_stale_levels(self):
        text=format_exit_alert('closed',position(),95)
        self.assertLess(len(text),180)
        self.assertNotIn('Last mark',text)
    async def test_liquidation_buffer_uses_authoritative_price(self):
        p=position(mark_price=95,liquidation_price=94,unrealized_pnl_pct=-20)
        self.assertAlmostEqual(p.buffer_to_liq_pts(),10)
    async def test_net_failure_is_not_mislabeled_gross(self):
        sdk_pos=NS(collateral=100,leverage=10,open_price=100,buy=True)
        with patch('app.veranta_sdk.position_net_pnl',side_effect=ValueError('bad fees')):
            net,gross=_pnl_for_sdk_position(sdk_pos,NS(),95)
        self.assertIsNone(net)
        self.assertEqual(gross,-50)
    async def test_shared_lazer_feed_updates_all_markets(self):
        client=VerantaSDK()
        client._pair_info={i:NS(lazer_feed=NS(feed_id=1)) for i in [1,116]}
        client._pair_symbols={1:'BTC/USD',116:'BTC_UPSIDE/USD'}
        client.ensure_pairs=AsyncMock()
        class Stream:
            async def run(self,callback):
                import time
                await callback(NS(feed_id=1,price=81500,timestamp_ms=time.time()*1000))
            def stop(self):pass
        with patch.object(client._client,'lazer_price_stream',return_value=Stream()),patch.object(client,'_store_lazer_mark'):
            result=await client._fetch_lazer_oneshot({1,116})
        await client.aclose()
        self.assertEqual(set(result),{1,116})
    async def test_upside_net_pnl_profit_share_and_signed_funding(self):
        sdk_pos=NS(collateral=100,leverage=10,open_price=100,buy=True,is_upside=True,
                   rollover_fee=1,unrealised_funding_fee=2,loss_protection_raw=0)
        pair=NS(close_fee_p=0,loss_protection_multiplier={},pnl_fees=NS(tier_p=[0],fees_p=[25]))
        net,gross=_pnl_for_sdk_position(sdk_pos,pair,110)
        self.assertEqual(gross,100)
        self.assertEqual(net,72) # 100 gross - 25 profit share - 1 rollover - 2 funding
        sdk_pos.unrealised_funding_fee=-2
        net,gross=_pnl_for_sdk_position(sdk_pos,pair,95)
        self.assertEqual(gross,-50)
        self.assertEqual(net,-49) # no profit share on losses; funding credit adds 2
    async def test_fixed_fee_short_net_pnl(self):
        sdk_pos=NS(collateral=100,leverage=10,open_price=100,buy=False,is_upside=False,
                   rollover_fee=1,unrealised_funding_fee=2,loss_protection_raw=0)
        pair=NS(close_fee_p=.1,loss_protection_multiplier={},pnl_fees=NS(tier_p=[],fees_p=[]))
        net,gross=_pnl_for_sdk_position(sdk_pos,pair,90)
        self.assertEqual(gross,100)
        self.assertEqual(net,96) # 100 - $1 close fee on $1000 notional - 1 - 2
    async def test_old_chart_identity_includes_wallet_and_entry(self):
        from app.formatting import positions_keyboard
        first=position(opened_at=None)
        keys=[positions_keyboard([p]).inline_keyboard[0][0].callback_data for p in
              [first,replace(first,open_price=110),replace(first,trader='0x'+'22'*20)]]
        self.assertEqual(len(set(keys)),3)
    async def test_failed_refresh_does_not_claim_position_closed(self):
        from app.handlers import on_callback
        bot=NS(send_message=AsyncMock())
        query=NS(data='chart:old',answer=AsyncMock())
        load=PositionsLoad('Could not refresh.',[],False,available=False)
        with patch('app.handlers._load_positions',AsyncMock(return_value=load)):
            await on_callback(NS(callback_query=query,effective_chat=NS(id=1)),NS(bot=bot))
        self.assertEqual(bot.send_message.call_count,1)
        self.assertNotIn('closed',bot.send_message.call_args.kwargs['text'])
    async def test_invalid_prices_have_no_liquidation_buffer(self):
        for value in [float('nan'),float('inf')]:
            self.assertIsNone(position(mark_price=value).buffer_to_liq_pts())
