"""Observed OHLC: UTC buckets, no interpolation or synthetic marks."""
from __future__ import annotations

import math
import time


def clean_ticks(ticks):
    valid = []
    for sample in ticks:
        try:
            ts, price = map(float, sample)
        except (TypeError, ValueError):
            continue
        if math.isfinite(ts) and math.isfinite(price) and ts > 0 and price > 0:
            valid.append((ts, price))
    return sorted(valid, key=lambda sample: sample[0])


def prepare_tape(ticks, *, now=None, window=7200):
    now = time.time() if now is None else now
    return [(ts, p) for ts, p in clean_ticks(ticks) if now - window <= ts <= now]


def build_candles(ticks, interval=15):
    if interval <= 0:
        raise ValueError('Candle interval must be positive')
    candles = []
    for ts, price in clean_ticks(ticks):
        bucket = int(ts // interval) * interval
        if not candles or candles[-1]['time'] != bucket:
            candles.append(dict(time=bucket, open=price, high=price, low=price,
                                close=price, count=1))
        else:
            candle = candles[-1]
            candle['high'] = max(candle['high'], price)
            candle['low'] = min(candle['low'], price)
            candle['close'] = price
            candle['count'] += 1
    return candles
