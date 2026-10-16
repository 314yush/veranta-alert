#!/usr/bin/env python3
"""Smoke-test SDK positions + fresh vs stale mark PnL (no Telegram).

Usage:
  python scripts/smoke_sdk_positions.py 0xYourWallet
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_sdk_positions.py 0xWallet", file=sys.stderr)
        raise SystemExit(2)

    wallet = sys.argv[1].strip()
    pk = (os.getenv("VERANTA_PRIVATE_KEY") or os.getenv("AVANTIS_PRIVATE_KEY") or "").strip()
    trader = (os.getenv("VERANTA_TRADER_ADDRESS") or os.getenv("AVANTIS_TRADER_ADDRESS") or "").strip()
    network = (os.getenv("VERANTA_NETWORK") or os.getenv("AVANTIS_NETWORK") or "mainnet").strip()

    missing = []
    if not pk:
        missing.append("AVANTIS_PRIVATE_KEY")
    if not trader:
        missing.append("AVANTIS_TRADER_ADDRESS")
    if missing:
        print(
            "Missing Veranta credentials: "
            + ", ".join(missing)
            + "\nGenerate a service API key at https://delegate.veranta.xyz/ "
            "and set them in .env (Model A).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    from app.veranta_sdk import VerantaSDK, _pnl_for_sdk_position

    print(f"network={network} trader(operator)={trader}")
    print(f"utc_now={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"fetching positions for watch wallet={wallet} …")

    sdk = VerantaSDK(
        private_key=pk,
        trader_address=trader,
        network=network,
    )
    try:
        await sdk.ensure_pairs()
        print(f"mark_max_age_seconds={sdk.mark_max_age_seconds}")

        print("mark source order: 1) lazer  2) feed-v3 (fresh only)  — hermes omitted")
        feed_rows = await sdk.fetch_feed_v3_last_prices()
        lazer = await sdk._fetch_lazer_oneshot({1}, timeout_s=5.0)
        # Optional diagnostic only (not used for marks)
        hermes = await sdk._fetch_hermes_marks({1})  # BTC

        btc_feed = feed_rows.get(1)
        btc_lazer = lazer.get(1)
        btc_hermes = hermes.get(1)

        print("--- BTC/USD marks ---")
        if btc_lazer:
            print(f"  lazer    c={btc_lazer[0]:.4f}  age_s={btc_lazer[1]:.2f}  PRIMARY")
        else:
            print("  lazer    (missing)")
        if btc_feed:
            print(
                f"  feed-v3  c={btc_feed[0]:.4f}  age_s={btc_feed[1]}"
                + (
                    "  STALE/REJECT"
                    if btc_feed[1] is not None
                    and btc_feed[1] > sdk.mark_max_age_seconds
                    else "  secondary-if-fresh"
                )
            )
        else:
            print("  feed-v3  (missing)")
        if btc_hermes:
            print(
                f"  hermes   c={btc_hermes[0]:.4f}  age_s={btc_hermes[1]:.2f}"
                "  (diagnostic only; not used)"
            )
        else:
            print("  hermes   (missing; unused)")

        positions = await sdk.fetch_positions(wallet)
        print(f"open positions: {len(positions)}")
        sources = sdk.last_mark_sources
        if sources:
            print(f"resolved mark sources: {sources}")

        # Raw SDK positions for stale-vs-fresh PnL comparison
        raw = await sdk.client.account.positions(trader=wallet)
        pair_info_btc = sdk._pair_info.get(1)

        for pos in positions:
            gross = pos.gross_pnl_usdc
            net = pos.unrealized_pnl_usdc
            net_pct = pos.unrealized_pnl_pct
            gross_pct = (
                (gross / pos.collateral_usdc * 100.0)
                if gross is not None and pos.collateral_usdc > 0
                else None
            )
            print(
                f"  {pos.symbol} {pos.side} idx={pos.index} "
                f"collateral={pos.collateral_usdc:.2f} lev={pos.leverage:.2f} "
                f"mark={pos.mark_price} open={pos.open_price:.6f}"
            )
            print(
                f"    gross=${gross:.4f} ({gross_pct:+.2f}%)  "
                f"net=${net:.4f} ({net_pct:+.2f}%)"
                if gross is not None
                and net is not None
                and net_pct is not None
                and gross_pct is not None
                else f"    gross={gross} net={net} net_pct={net_pct}"
            )

            if pos.pair_index == 1 and btc_feed and pos.mark_price:
                sdk_pos = next(
                    (
                        p
                        for p in raw.positions
                        if int(p.pair_index) == pos.pair_index
                        and int(p.index) == pos.index
                    ),
                    None,
                )
                if sdk_pos is not None:
                    stale_mark = btc_feed[0]
                    stale_net, stale_gross = _pnl_for_sdk_position(
                        sdk_pos, pair_info_btc, stale_mark
                    )
                    print(
                        f"    compare stale feed-v3 mark={stale_mark:.4f} "
                        f"net=${stale_net:.4f} | fresh mark={pos.mark_price:.4f} "
                        f"net=${net:.4f}"
                        if stale_net is not None and net is not None
                        else f"    compare stale_net={stale_net} fresh_net={net}"
                    )
    finally:
        await sdk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
