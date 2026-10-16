"""Per-chat alert preferences and PnL%-of-collateral distance helpers.

All user-facing distances are leverage-normalized PnL percentage-points of
collateral (not raw mark-vs-level price %). At 5x and 250x, “within 10 PnL
pts of TP/SL” means the same margin-risk language.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Sequence

# Defaults (also used when migrating existing chats with no prefs row).
DEFAULT_LIQ_LEVELS: tuple[float, ...] = (-40.0, -60.0, -75.0)
DEFAULT_TP_DISTANCE_PNL = 10.0
DEFAULT_SL_DISTANCE_PNL = 10.0

LIQ_PRESETS: dict[str, tuple[float, ...]] = {
    "safe": (-30.0, -50.0, -70.0),
    "default": (-40.0, -60.0, -75.0),
    "tight": (-60.0, -75.0, -82.0),
}

DISTANCE_PRESETS: tuple[float, ...] = (5.0, 10.0, 20.0)


@dataclass
class AlertPrefs:
    chat_id: int
    liq_enabled: bool = True
    tp_enabled: bool = True
    sl_enabled: bool = True
    liq_levels: tuple[float, ...] = DEFAULT_LIQ_LEVELS
    tp_distance_pnl: float = DEFAULT_TP_DISTANCE_PNL
    sl_distance_pnl: float = DEFAULT_SL_DISTANCE_PNL

    def with_updates(self, **kwargs: object) -> AlertPrefs:
        return replace(self, **kwargs)  # type: ignore[arg-type]


def default_prefs(chat_id: int) -> AlertPrefs:
    return AlertPrefs(chat_id=chat_id)


def normalize_liq_levels(levels: Sequence[float]) -> tuple[float, ...]:
    """Sort warn → critical (least negative first). Deduplicate."""
    cleaned = sorted({float(x) for x in levels}, reverse=True)
    if not cleaned:
        return DEFAULT_LIQ_LEVELS
    return tuple(cleaned)


def liq_levels_to_json(levels: Sequence[float]) -> str:
    return json.dumps(list(normalize_liq_levels(levels)))


def liq_levels_from_json(raw: str | None) -> tuple[float, ...]:
    if not raw:
        return DEFAULT_LIQ_LEVELS
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return DEFAULT_LIQ_LEVELS
    if not isinstance(data, list) or not data:
        return DEFAULT_LIQ_LEVELS
    try:
        return normalize_liq_levels(float(x) for x in data)
    except (TypeError, ValueError):
        return DEFAULT_LIQ_LEVELS


def format_liq_levels(levels: Sequence[float]) -> str:
    parts: list[str] = []
    for lv in normalize_liq_levels(levels):
        if float(lv).is_integer():
            parts.append(str(int(lv)))
        else:
            parts.append(f"{lv:g}")
    return "/".join(parts)


def cycle_liq_preset(current: Sequence[float]) -> tuple[str, tuple[float, ...]]:
    """Return next (name, levels) after the preset matching `current`, else default."""
    order = ("safe", "default", "tight")
    cur = normalize_liq_levels(current)
    idx = 0
    for i, name in enumerate(order):
        if normalize_liq_levels(LIQ_PRESETS[name]) == cur:
            idx = (i + 1) % len(order)
            break
    else:
        idx = order.index("default")
    name = order[idx]
    return name, LIQ_PRESETS[name]


def cycle_distance(current: float, presets: Sequence[float] = DISTANCE_PRESETS) -> float:
    vals = [float(x) for x in presets]
    try:
        i = vals.index(float(current))
        return vals[(i + 1) % len(vals)]
    except ValueError:
        return float(vals[1] if len(vals) > 1 else vals[0])


# ---------------------------------------------------------------------------
# Distance math (PnL% of collateral) — pure helpers for alerts + unit asserts
# ---------------------------------------------------------------------------


def gross_pnl_usdc(
    *,
    current_price: float,
    open_price: float,
    collateral: float,
    leverage: float,
    is_long: bool,
) -> float | None:
    """Mirror SDK gross_pnl; return None on invalid inputs."""
    if (
        current_price <= 0
        or open_price <= 0
        or collateral <= 0
        or leverage <= 0
    ):
        return None
    direction = 1.0 if is_long else -1.0
    return (current_price - open_price) * direction / open_price * leverage * collateral


def pnl_pct_at_price(
    *,
    price: float,
    open_price: float,
    collateral: float,
    leverage: float,
    is_long: bool,
) -> float | None:
    """Gross PnL% of collateral if mark were `price` (target-level language)."""
    g = gross_pnl_usdc(
        current_price=price,
        open_price=open_price,
        collateral=collateral,
        leverage=leverage,
        is_long=is_long,
    )
    if g is None or collateral <= 0:
        return None
    return g / collateral * 100.0


def remaining_to_tp_pnl(current_pnl_pct: float, tp_pnl_pct: float) -> float:
    """PnL percentage-points still needed to reach TP (positive = not there yet).

    Works for long and short: TP is the profitable direction, so tp_pnl_pct
    sits above current_pnl_pct while approaching.
    """
    return float(tp_pnl_pct) - float(current_pnl_pct)


def remaining_to_sl_pnl(current_pnl_pct: float, sl_pnl_pct: float) -> float:
    """PnL percentage-points remaining before hitting SL (positive = not there yet).

    SL is the loss direction (more negative / worse PnL%), so remaining shrinks
    as current_pnl_pct falls toward sl_pnl_pct.
    """
    return float(current_pnl_pct) - float(sl_pnl_pct)


def within_pnl_distance(remaining_pts: float, distance_pnl: float) -> bool:
    """True when remaining pts are in [0, distance] (approaching or at target)."""
    if distance_pnl < 0:
        return False
    return 0.0 <= float(remaining_pts) <= float(distance_pnl)


def tp_sl_target_pnl_pct(
    *,
    target_price: float,
    open_price: float,
    collateral: float,
    leverage: float,
    is_long: bool,
) -> float | None:
    """PnL% of collateral at a TP or SL price level."""
    if target_price <= 0:
        return None
    return pnl_pct_at_price(
        price=target_price,
        open_price=open_price,
        collateral=collateral,
        leverage=leverage,
        is_long=is_long,
    )
