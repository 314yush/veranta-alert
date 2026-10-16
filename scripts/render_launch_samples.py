#!/usr/bin/env python3
"""Render launch_long.png / launch_short.png from the live watch wallet."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

WALLET = os.getenv(
    "WATCH_WALLET", "0x791D41b061891e7701461D672a0989abF1bc4005"
)
OUT_DIR = Path(os.getenv("QA_CHART_DIR", "/workspace/qa_charts"))


def _synthetic(side: str):
    from app.veranta_sdk import Position

    is_long = side.upper() == "LONG"
    open_px = 1.006575 if is_long else 1.00597
    mark = 1.0104 if is_long else 1.0104
    liq = 0.994883 if is_long else 1.01779
    tp = 1.34 if is_long else 0.67
    coll = 10.0
    lev = 72.76
    # rough gross PnL
    direction = 1.0 if is_long else -1.0
    pnl = coll * lev * direction * (mark - open_px) / open_px
    return Position(
        trader=WALLET,
        pair_index=118,
        index=1 if is_long else 0,
        is_long=is_long,
        collateral_usdc=coll,
        leverage=lev,
        open_price=open_px,
        tp=tp,
        sl=0.0,
        liquidation_price=liq,
        mark_price=mark,
        symbol="XRP_UPSIDE/USD",
        unrealized_pnl_usdc=pnl,
        unrealized_pnl_pct=(pnl / coll) * 100.0,
    )


async def _collect_ticks(sdk, pair_indexes: set[int], seconds: float) -> int:
    from app.ticks import tick_count

    if not pair_indexes:
        return 0
    await sdk.fetch_mark_prices_for_pairs(pair_indexes)
    t_end = time.time() + seconds
    while time.time() < t_end:
        await asyncio.sleep(1.0)
    return sum(tick_count(i) for i in pair_indexes)


async def main() -> int:
    from app.veranta_sdk import VerantaSDK
    from app.chart import chart_from_position, render_risk_card
    from app.ohlc import binance_symbol_for, is_exotic_pair
    from app.ticks import tick_count

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pk = (os.getenv("VERANTA_PRIVATE_KEY") or os.getenv("AVANTIS_PRIVATE_KEY") or "").strip()
    trader = (os.getenv("VERANTA_TRADER_ADDRESS") or os.getenv("AVANTIS_TRADER_ADDRESS") or "").strip()
    network = (os.getenv("VERANTA_NETWORK") or os.getenv("AVANTIS_NETWORK") or "mainnet").strip()

    positions = []
    sdk = None
    if pk and trader:
        sdk = VerantaSDK(
            private_key=pk,
            trader_address=trader,
            network=network,
        )
        try:
            print(f"fetching positions for {WALLET} …")
            positions = await sdk.fetch_positions(WALLET)
            print(f"open positions: {len(positions)}")
            for p in positions:
                print(
                    f"  {p.symbol} {p.side} idx={p.index} pair={p.pair_index} "
                    f"mark={p.mark_price} pnl={p.unrealized_pnl_usdc} "
                    f"pct={p.unrealized_pnl_pct}"
                )
            needed = {p.pair_index for p in positions} or {118}
            n = await _collect_ticks(sdk, needed, seconds=float(os.getenv("TICK_WAIT_S", "25")))
            print(f"lazer ticks collected (sum over pairs) ≈ {n}")
            for idx in sorted(needed):
                print(f"  pair {idx} ticks={tick_count(idx)}")
        except Exception as exc:  # noqa: BLE001
            print(f"live fetch failed: {exc!r}")
            positions = []

    longs = [p for p in positions if p.is_long]
    shorts = [p for p in positions if not p.is_long]
    long_pos = longs[0] if longs else _synthetic("LONG")
    short_pos = shorts[0] if shorts else _synthetic("SHORT")
    source = "live" if positions else "synthetic"
    print(f"rendering source={source}")

    for label, pos in (("launch_long.png", long_pos), ("launch_short.png", short_pos)):
        exotic = is_exotic_pair(pos.symbol)
        mapped = binance_symbol_for(pos.symbol)
        print(f"{label}: symbol={pos.symbol} exotic={exotic} binance={mapped}")
        png = chart_from_position(pos)
        if not png:
            png = render_risk_card(
                pair_label=pos.symbol,
                side=pos.side,
                mark=pos.mark_price,
                open_price=pos.open_price,
                liquidation=pos.liquidation_price,
                tp=pos.tp or None,
                sl=pos.sl or None,
                pnl_usdc=pos.unrealized_pnl_usdc,
                pnl_pct=pos.unrealized_pnl_pct,
                collateral=pos.collateral_usdc,
                leverage=pos.leverage,
                is_long=pos.is_long,
                pair_index=pos.pair_index,
                buffer_pts=pos.buffer_to_liq_pts(),
            )
        if not png:
            print(f"FAIL: no png for {label}")
            if sdk:
                await sdk.aclose()
            return 1
        dest = OUT_DIR / label
        dest.write_bytes(png)
        print(f"wrote {dest} bytes={len(png)}")

    if sdk:
        await sdk.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
