import tempfile
import time
import unittest
from pathlib import Path
from app import ticks, ohlc


class TapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ticks.configure(Path(self.tmp.name) / 'tape.db')
        self.now = time.time()

    def tearDown(self):
        ticks.close()
        self.tmp.cleanup()

    def test_preserves_subsecond_price_spike(self):
        for offset, price in [(0, 100), (.1, 110), (.2, 95), (.3, 101)]:
            ticks.record_tick(1, price, self.now + offset - 1)
        self.assertEqual([p for _, p in ticks.load_ticks(1, limit=0)], [100, 110, 95, 101])

    def test_invalid_samples_rejected(self):
        for price in [float('nan'), float('inf'), -1, 0]:
            self.assertFalse(ticks.record_tick(2, price, self.now))
        self.assertFalse(ticks.record_tick(2, 100, float('inf')))

    def test_single_sample_limit(self):
        ticks.record_tick(3, 100, self.now - 20)
        ticks.record_tick(3, 101, self.now - 10)
        self.assertEqual(ticks.load_ticks(3, limit=1), [(self.now - 10, 101)])

    def test_short_exchange_defaults(self):
        self.assertEqual(ohlc.DEFAULT_INTERVAL, '5m')
        self.assertLessEqual(ohlc.DEFAULT_LIMIT, 30)
        self.assertLessEqual(ohlc.CACHE_TTL_S, 5)


class CandleTests(unittest.TestCase):
    def build(self, samples, interval=5):
        from app.candles import build_candles
        return build_candles(samples, interval)

    def test_sorted_ohlc_and_missing_buckets(self):
        result = self.build([(104, 101), (101, 100), (102, 110), (103, 95), (115, 102)])
        self.assertEqual(result, [
            dict(time=100, open=100, high=110, low=95, close=101, count=4),
            dict(time=115, open=102, high=102, low=102, close=102, count=1),
        ])

    def test_boundary_and_invalid_samples(self):
        result = self.build([(104.99, 100), (105, 101), (106, float('nan')), (107, -2)])
        self.assertEqual([c['time'] for c in result], [100, 105])

    def test_window_does_not_pin_stale_prices_to_now(self):
        from app.candles import prepare_tape
        self.assertEqual(prepare_tape([(100, 100)], now=10000), [])
        self.assertEqual(prepare_tape([(999, 100), (1001, 200)], now=1000), [(999, 100)])


class RenderTests(unittest.TestCase):
    def test_local_tape_skips_external_fetch(self):
        from unittest.mock import patch
        from app.chart import render_levels_chart
        from PIL import Image
        import io
        now = time.time()
        with patch('app.chart.fetch_candles', side_effect=AssertionError('Should prefer observed prices')):
            png = render_levels_chart(pair_label='BTC', side='LONG', mark=100,
                    open_price=90, liquidation=80, ticks=[(now-5, 100), (now, 101)])
        self.assertEqual(Image.open(io.BytesIO(png)).size, (1080, 720))

    def test_duration_preview_settings(self):
        from app.ticket import render_ticket
        from unittest.mock import patch
        from app.candles import build_candles
        now = time.time()
        for duration, interval in [(120, 5), (300, 15), (600, 30), (36000, 1800)]:
            tape = [(now-duration+1, 100), (now, 101)]
            with self.subTest(duration=duration), patch('app.ticket.build_candles', wraps=build_candles) as build:
                png = render_ticket(pair_label='XAU', side='LONG', mark=101,
                                    open_price=100, liquidation=90, ticks=tape,
                                    candle_interval_s=interval, window_s=duration,
                                    chart_label='SIMULATED PREVIEW')
                self.assertTrue(png.startswith(b'\x89PNG'))
                self.assertEqual(build.call_args.args, (tape, interval))

    def test_tight_candle_geometry(self):
        from app.ticket import _candle_width, _tape_range
        self.assertAlmostEqual(_candle_width(40), 32.8)
        lo, hi = _tape_range([100, 110])
        self.assertAlmostEqual(hi-lo, 11.2)

    def test_small_price_moves_fill_chart(self):
        from app.ticket import _tape_range
        lo, hi = _tape_range([4165, 4165.01])
        self.assertLess(hi-lo, .02)

    def test_empty_flat_sparse_and_stale_render(self):
        from app.ticket import render_ticket
        from PIL import Image
        import io
        now = time.time()
        cases = [[], [(now, 100)], [(now-60, 100), (now, 100)],
                 [(now-120, 100), (now-60, 101)]]
        for tape in cases:
            with self.subTest(tape=tape):
                png = render_ticket(pair_label='XAU', side='SHORT', mark=100,
                                    open_price=1, liquidation=10000, tp=.1, ticks=tape)
                self.assertEqual(Image.open(io.BytesIO(png)).size, (1080,720))


if __name__ == '__main__':
    unittest.main()
