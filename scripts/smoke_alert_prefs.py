#!/usr/bin/env python3
"""Smoke + unit-style asserts for alert prefs defaults and PnL-distance math."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.prefs import (  # noqa: E402
    DEFAULT_LIQ_LEVELS,
    DEFAULT_SL_DISTANCE_PNL,
    DEFAULT_TP_DISTANCE_PNL,
    LIQ_PRESETS,
    cycle_liq_preset,
    format_liq_levels,
    pnl_pct_at_price,
    remaining_to_sl_pnl,
    remaining_to_tp_pnl,
    tp_sl_target_pnl_pct,
    within_pnl_distance,
)
from app.db import Database  # noqa: E402


def test_math_leverage_normalized() -> None:
    """5x vs 250x: same remaining PnL-pts language for TP/SL proximity."""
    open_px = 100.0
    coll = 100.0

    # Long: TP at +4% price → at 5x = +20% PnL, at 250x = +1000% PnL
    tp_price = 104.0
    for lev in (5.0, 250.0):
        tp_pnl = pnl_pct_at_price(
            price=tp_price,
            open_price=open_px,
            collateral=coll,
            leverage=lev,
            is_long=True,
        )
        assert tp_pnl is not None
        # Current: halfway there in PnL space → remaining = 10 for both once we
        # choose current = tp_pnl - 10.
        current = tp_pnl - 10.0
        rem = remaining_to_tp_pnl(current, tp_pnl)
        assert abs(rem - 10.0) < 1e-9, (lev, rem)
        assert within_pnl_distance(rem, 10.0)
        assert not within_pnl_distance(rem, 5.0)

    # Short SL: price up 2% → loss; remaining math identical in PnL pts.
    sl_price = 102.0
    for lev in (5.0, 250.0):
        sl_pnl = tp_sl_target_pnl_pct(
            target_price=sl_price,
            open_price=open_px,
            collateral=coll,
            leverage=lev,
            is_long=False,
        )
        assert sl_pnl is not None
        # Short: price up → negative PnL
        assert sl_pnl < 0
        current = sl_pnl + 10.0  # 10 pts above SL (safer)
        rem = remaining_to_sl_pnl(current, sl_pnl)
        assert abs(rem - 10.0) < 1e-9, (lev, rem, sl_pnl)
        assert within_pnl_distance(rem, 10.0)

    # Explicit: remaining does not depend on leverage once PnL%s are known.
    assert remaining_to_tp_pnl(20.0, 30.0) == 10.0
    assert remaining_to_sl_pnl(-50.0, -60.0) == 10.0
    assert within_pnl_distance(0.0, 10.0)
    assert within_pnl_distance(10.0, 10.0)
    assert not within_pnl_distance(-0.1, 10.0)  # past target
    assert not within_pnl_distance(10.1, 10.0)

    # Long TP vs short TP signs
    long_tp = pnl_pct_at_price(
        price=110.0, open_price=100.0, collateral=50.0, leverage=5.0, is_long=True
    )
    short_tp = pnl_pct_at_price(
        price=90.0, open_price=100.0, collateral=50.0, leverage=5.0, is_long=False
    )
    assert long_tp is not None and short_tp is not None
    assert abs(long_tp - 50.0) < 1e-9  # 10% * 5x
    assert abs(short_tp - 50.0) < 1e-9


def test_presets() -> None:
    assert DEFAULT_LIQ_LEVELS == (-40.0, -60.0, -75.0)
    assert DEFAULT_TP_DISTANCE_PNL == 10.0
    assert DEFAULT_SL_DISTANCE_PNL == 10.0
    name, levels = cycle_liq_preset(DEFAULT_LIQ_LEVELS)
    assert name == "tight"
    assert levels == LIQ_PRESETS["tight"]
    name2, _ = cycle_liq_preset(levels)
    assert name2 == "safe"
    assert format_liq_levels((-75, -40, -60)) == "-40/-60/-75"


async def test_db_defaults_migrate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db = Database(db_path)
        await db.connect()
        # Legacy-style: subscription without prefs row (simulate old DB)
        await db.upsert_subscription(42, "0x" + "ab" * 20)
        prefs = await db.get_alert_prefs(42)
        assert prefs.liq_enabled and prefs.tp_enabled and prefs.sl_enabled
        assert prefs.liq_levels == DEFAULT_LIQ_LEVELS
        assert prefs.tp_distance_pnl == 10.0
        assert prefs.sl_distance_pnl == 10.0

        prefs = prefs.with_updates(
            liq_enabled=False,
            tp_distance_pnl=5.0,
            liq_levels=LIQ_PRESETS["safe"],
        )
        await db.save_alert_prefs(prefs)
        again = await db.get_alert_prefs(42)
        assert again.liq_enabled is False
        assert again.tp_distance_pnl == 5.0
        assert again.liq_levels == LIQ_PRESETS["safe"]

        # Re-upsert wallet must NOT wipe prefs
        await db.upsert_subscription(42, "0x" + "cd" * 20)
        kept = await db.get_alert_prefs(42)
        assert kept.liq_enabled is False
        assert kept.tp_distance_pnl == 5.0

        await db.close()


def main() -> None:
    test_math_leverage_normalized()
    print("OK math: 5x vs 250x same PnL-point distance logic")
    test_presets()
    print("OK presets/defaults")
    asyncio.run(test_db_defaults_migrate())
    print("OK db migrate + prefs persistence")
    print("All alert-prefs smokes passed.")


if __name__ == "__main__":
    main()
