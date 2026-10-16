import time
import unittest
from io import BytesIO
from PIL import Image
from test_reliability import position
from app.position_chart import normalize_candles, level_layout, render_chart

class Charts(unittest.TestCase):
    def test_tradingview_ohlc_is_parsed(self):
        now=int(time.time())
        rows=normalize_candles(dict(t=[now-300,now],o=[99,100],h=[101,102],l=[98,99],c=[100,101]))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['close'],101)
    def test_stale_or_invalid_candles_are_rejected(self):
        self.assertEqual(normalize_candles([dict(time=1,open=100,high=101,low=99,close=100)]),[])
        self.assertEqual(normalize_candles([dict(time=time.time(),open=100,high=90,low=99,close=100)]),[])
    def test_far_tp_is_labeled_without_flattening_candles(self):
        levels=level_layout(position(tp=10000,sl=94),90,110)
        self.assertEqual({x['label'] for x in levels},{'ENTRY','MARK','LIQ','TP','SL'})
        tp=next(x for x in levels if x['label']=='TP')
        self.assertEqual(tp['outside'],'above')
        self.assertEqual(tp['price'],10000)
    def test_native_chart_renders_without_browser_or_history(self):
        data=render_chart(position(tp=110,sl=94),[],'History unavailable')
        im=Image.open(BytesIO(data))
        self.assertEqual(im.size,(1100,680))
