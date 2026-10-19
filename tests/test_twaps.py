import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from test_reliability import position

WALLET='0x'+'11'*20

def order(**changes):
    now=int(time.time()*1000)
    return dict(twap=dict(twapId=1,canceled=False,completed=False,trader=WALLET,
                         pairIndex=1,buy=True,isOpeningTwap=True,totalSlices=4,
                         timestampMillis=now-60000,**changes),trades=[])

def trade(slice_id=1,index=0):
    return dict(twapId=1,sliceId=slice_id,trader=WALLET,pairIndex=1,index=index,
                timestampMillis=int(time.time()*1000)-1000,
                transactionHash='0x'+'a'*64,isOpeningFlow=True)

def active(raw):
    from app.twaps import parse_active_twaps
    return parse_active_twaps([raw],WALLET,{1:'BTC/USD'})

class TwapParsing(unittest.TestCase):
    def test_unfilled_and_partial_are_visible(self):
        self.assertEqual(active(order())[0].filled_slices,0)
        raw=order();raw['trades']=[trade(),trade()]
        self.assertEqual(active(raw)[0].filled_slices,1)

    def test_terminal_and_fully_executed_are_hidden(self):
        for field in ['completed','canceled']:
            raw=order();raw['twap'][field]=True
            self.assertEqual(active(raw),[])
        raw=order();raw['trades']=[trade(i) for i in range(1,5)]
        self.assertEqual(active(raw),[])

    def test_wrong_wallet_and_scheduled_not_shown(self):
        raw=order();raw['twap']['trader']='0x'+'22'*20
        self.assertEqual(active(raw),[])
        raw=order();raw['twap']['timestampMillis']=int(time.time()*1000)+60000
        self.assertEqual(active(raw),[])

    def test_only_confirmed_unique_slices_count(self):
        raw=order();raw['trades']=[trade(),trade(),trade(2)|{'transactionHash':None},trade(3)|{'twapId':2}]
        self.assertEqual(active(raw)[0].filled_slices,1)

class TwapDisplay(unittest.TestCase):
    def test_partial_annotates_existing_position_without_duplicate(self):
        from app.formatting import format_positions
        raw=order();raw['trades']=[trade()]
        text=format_positions([position(symbol='BTC/USD')],WALLET,twaps=active(raw))
        self.assertEqual(text.count('<b>BTC LONG</b>'),1)
        self.assertIn('1/4 slices',text)
        self.assertIn('1 open',text)

    def test_unfilled_order_is_not_a_position_or_pnl(self):
        from app.formatting import format_positions
        text=format_positions([],WALLET,twaps=active(order()))
        self.assertIn('TWAP',text);self.assertIn('0/4 slices',text)
        self.assertNotIn('No open positions',text)
        self.assertNotIn('Net PnL',text)

    def test_completion_returns_to_normal_position(self):
        from app.formatting import format_positions
        raw=order();raw['twap']['completed']=True
        text=format_positions([position(symbol='BTC/USD')],WALLET,twaps=active(raw))
        self.assertNotIn('TWAP',text);self.assertIn('1 open',text)

    def test_twap_rows_paginate_without_chart_buttons(self):
        from app.formatting import format_positions,positions_keyboard
        rows=[]
        for i in range(10):
            raw=order();raw['twap']['twapId']=i+1;rows+=active(raw)
        text=format_positions([],WALLET,page=1,twaps=rows)
        self.assertEqual(text.count('0/4 slices'),2)
        buttons=positions_keyboard([],1,twaps=rows).inline_keyboard
        self.assertTrue(any(b.callback_data=='positions:0' for line in buttons for b in line))
        self.assertFalse(any((b.callback_data or '').startswith('chart:') for line in buttons for b in line))

    def test_closing_twap_annotates_remaining_position(self):
        from app.formatting import format_positions
        raw=order();raw['twap']['isOpeningTwap']=False
        raw['trades']=[trade() | {'isOpeningFlow':False}]
        text=format_positions([position(symbol='BTC/USD')],WALLET,twaps=active(raw))
        self.assertIn('TWAP closing · partially filled · 1/4 slices',text)
        self.assertEqual(text.count('<b>BTC LONG</b>'),1)

    def test_reused_position_slot_not_linked_to_old_twap(self):
        from app.twaps import position_rows
        raw=order();raw['trades']=[trade()]
        rows=position_rows([position(opened_at=int(time.time())+10)],active(raw))
        self.assertEqual(len(rows),2)

class TwapLoad(unittest.IsolatedAsyncioTestCase):
    async def test_twap_failure_keeps_positions_and_flags_partial_data(self):
        from app.handlers import _load_positions
        client=NS(fetch_positions=AsyncMock(return_value=[position(symbol='BTC/USD')]),
                  fetch_active_twaps=AsyncMock(side_effect=RuntimeError('unavailable')))
        db=NS(get_subscription=AsyncMock(return_value=NS(wallet=WALLET,muted=False)))
        with patch('app.handlers._client',return_value=client),patch('app.handlers._db',return_value=db):
            load=await _load_positions(NS(),1)
        self.assertTrue(load.available);self.assertIn('BTC LONG',load.html)
        self.assertIn('TWAP status unavailable',load.html)

    async def test_fetch_scans_past_completed_pages(self):
        from app.veranta_sdk import VerantaSDK
        sdk=VerantaSDK();sdk.ensure_pairs=AsyncMock();sdk._pair_symbols={1:'BTC/USD'}
        completed=[]
        for i in range(50):
            raw=order();raw['twap'].update(twapId=i+10,completed=True);completed.append(raw)
        with patch.object(sdk.client.account,'twaps',AsyncMock(side_effect=[completed,[order()]])) as fetch:
            result=await sdk.fetch_active_twaps(WALLET)
            self.assertEqual(len(result),1)
            self.assertEqual([c.kwargs['page'] for c in fetch.call_args_list],[0,1])
        await sdk.aclose()
