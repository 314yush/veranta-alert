#!/usr/bin/env python3
"""Smoke: render a BTC LW chart (or Pillow fallback) to /workspace/avantis_lw_chart_sample.png."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = Path("/workspace/avantis_lw_chart_sample.png")


def main() -> int:
    from app.chart import render_levels_chart
    from app.ohlc import binance_symbol_for, fetch_candles

    pair = "BTC/USD"
    print("binance_map", pair, "->", binance_symbol_for(pair))
    t_ohlc = time.perf_counter()
    candles = fetch_candles(pair)
    print(
        "ohlc",
        f"n={len(candles) if candles else 0}",
        f"elapsed_ms={(time.perf_counter() - t_ohlc) * 1000:.0f}",
    )

    # Realistic-ish levels around last close (or fixed if no candles).
    if candles:
        last = float(candles[-1]["close"])
    else:
        last = 65000.0
    open_price = last * 0.985
    mark = last
    liq = last * 0.92
    sl = last * 0.94
    tp = last * 1.25  # intentionally far — should not break scale on LW/Pillow

    t0 = time.perf_counter()
    png = render_levels_chart(
        pair_label=pair,
        side="LONG",
        mark=mark,
        open_price=open_price,
        liquidation=liq,
        tp=tp,
        sl=sl,
        pnl_usdc=-42.5,
    )
    elapsed = time.perf_counter() - t0
    if not png:
        print("FAIL: no PNG produced")
        return 1

    OUT.write_bytes(png)
    # Warm second render (OHLC cache + chrome cold→warm)
    t1 = time.perf_counter()
    png2 = render_levels_chart(
        pair_label=pair,
        side="LONG",
        mark=mark,
        open_price=open_price,
        liquidation=liq,
        tp=tp,
        sl=sl,
        pnl_usdc=-42.5,
    )
    warm = time.perf_counter() - t1
    print(
        f"OK path={OUT} bytes={len(png)} cold_s={elapsed:.3f} warm_s={warm:.3f} "
        f"second_bytes={len(png2) if png2 else 0}"
    )
    # Also exercise unmapped fallback quickly
    t2 = time.perf_counter()
    fx = render_levels_chart(
        pair_label="XAU/USD",
        side="SHORT",
        mark=2400.0,
        open_price=2380.0,
        liquidation=2450.0,
        tp=2200.0,
        sl=2420.0,
        pnl_usdc=12.0,
    )
    print(
        f"fallback_XAU bytes={len(fx) if fx else 0} "
        f"elapsed_s={time.perf_counter() - t2:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
