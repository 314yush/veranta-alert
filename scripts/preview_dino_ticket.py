#!/usr/bin/env python3
"""One-off: render a ticket PNG to confirm the dino wordmark composites."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ticket import render_ticket  # noqa: E402

def main() -> int:
    ticks = [(time.time() - 60 + i, 63000 + i * 8) for i in range(60)]
    png = render_ticket(
        pair_label="BTC/USD",
        side="LONG",
        mark=63480,
        open_price=63120,
        liquidation=59800,
        tp=68000,
        sl=62000,
        pnl_usdc=124.5,
        pnl_pct=12.4,
        collateral=1000,
        leverage=10,
        is_long=True,
        pair_index=0,
        buffer_pts=97.4,
        ticks=ticks,
    )
    if not png:
        print("render_ticket returned None", file=sys.stderr)
        return 1
    out = ROOT / "data" / "ticket-dino-preview.png"
    out.write_bytes(png)
    print(f"wrote {out} bytes={len(png)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
