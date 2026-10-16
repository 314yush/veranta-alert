"""Proximity / exit alert decision logic with cooldown + hysteresis."""

from __future__ import annotations

import json
import math
import logging
import time
from dataclasses import dataclass

from .veranta_sdk import Position
from .config import LIQ_PNL_PERCENT, PNL_ALERT_LEVELS
from .db import Database
from .formatting import (
    format_exit_alert,
    format_liq_alert,
    format_proximity_alert,
)
from .prefs import (
    AlertPrefs,
    pnl_pct_at_price,
    remaining_to_sl_pnl,
    remaining_to_tp_pnl,
    tp_sl_target_pnl_pct,
    within_pnl_distance,
)

logger = logging.getLogger(__name__)

# If mark is within this % of TP/SL when the position vanishes, classify as that exit.
EXIT_NEAR_PCT = 1.5

TP_PROX_LEVEL = "tp_prox"
SL_PROX_LEVEL = "sl_prox"


@dataclass
class OutgoingAlert:
    text: str
    kind: str
    # When set, monitor should send_photo with this caption (= text).
    chart_png: bytes | None = None
    attach_chart: bool = False
    position: Position | None = None


def _near(mark: float, target: float, pct: float = EXIT_NEAR_PCT) -> bool:
    if mark <= 0 or target <= 0:
        return False
    return abs(mark - target) / mark * 100.0 <= pct


def _last_pnl_pct(prev: Position, mark: float) -> float | None:
    """Prefer snapshot net PnL%; else estimate gross PnL% at mark."""
    if prev.unrealized_pnl_pct is not None:
        return float(prev.unrealized_pnl_pct)
    return pnl_pct_at_price(
        price=mark,
        open_price=prev.open_price,
        collateral=prev.collateral_usdc,
        leverage=prev.leverage,
        is_long=prev.is_long,
    )


def _near_tp_ok(prev: Position, mark: float, pnl_pct: float | None) -> bool:
    """Near TP only if mark is on the profit side of entry and PnL agrees."""
    if prev.tp <= 0 or not _near(mark, prev.tp):
        return False
    if prev.is_long and mark < prev.open_price:
        return False
    if (not prev.is_long) and mark > prev.open_price:
        return False
    if pnl_pct is None:
        return False
    tp_pnl = tp_sl_target_pnl_pct(
        target_price=prev.tp,
        open_price=prev.open_price,
        collateral=prev.collateral_usdc,
        leverage=prev.leverage,
        is_long=prev.is_long,
    )
    if tp_pnl is None:
        return False
    # Remaining PnL-pts to TP should already be tiny / crossed.
    return remaining_to_tp_pnl(pnl_pct, tp_pnl) <= 5.0


def _near_sl_ok(prev: Position, mark: float, pnl_pct: float | None) -> bool:
    """Near SL only if mark is on the loss side of entry and PnL agrees."""
    if prev.sl <= 0 or not _near(mark, prev.sl):
        return False
    if prev.is_long and mark > prev.open_price:
        return False
    if (not prev.is_long) and mark < prev.open_price:
        return False
    if pnl_pct is None:
        return False
    sl_pnl = tp_sl_target_pnl_pct(
        target_price=prev.sl,
        open_price=prev.open_price,
        collateral=prev.collateral_usdc,
        leverage=prev.leverage,
        is_long=prev.is_long,
    )
    if sl_pnl is None:
        return False
    return remaining_to_sl_pnl(pnl_pct, sl_pnl) <= 5.0


def classify_exit(
    prev: Position,
    mark: float | None,
    *,
    liq_pnl_percent: float | None = None,
) -> str:
    """A missing position proves closure, not the execution cause."""
    return "closed"


def position_to_json(pos: Position) -> str:
    return json.dumps(
        {
            "trader": pos.trader,
            "pair_index": pos.pair_index,
            "index": pos.index,
            "is_long": pos.is_long,
            "collateral_usdc": pos.collateral_usdc,
            "leverage": pos.leverage,
            "open_price": pos.open_price,
            "tp": pos.tp,
            "sl": pos.sl,
            "liquidation_price": pos.liquidation_price,
            "mark_price": pos.mark_price,
            "symbol": pos.symbol,
            "opened_at": pos.opened_at,
            "unrealized_pnl_usdc": pos.unrealized_pnl_usdc,
            "unrealized_pnl_pct": pos.unrealized_pnl_pct,
        }
    )


def position_from_json(raw: str) -> Position:
    d = json.loads(raw)
    return Position(
        trader=d["trader"],
        pair_index=int(d["pair_index"]),
        index=int(d["index"]),
        is_long=bool(d["is_long"]),
        collateral_usdc=float(d["collateral_usdc"]),
        leverage=float(d["leverage"]),
        open_price=float(d["open_price"]),
        tp=float(d.get("tp") or 0),
        sl=float(d.get("sl") or 0),
        liquidation_price=float(d.get("liquidation_price") or 0),
        mark_price=d.get("mark_price"),
        symbol=d.get("symbol") or f"PAIR#{d['pair_index']}",
        opened_at=d.get("opened_at"),
        unrealized_pnl_usdc=d.get("unrealized_pnl_usdc"),
        unrealized_pnl_pct=d.get("unrealized_pnl_pct"),
    )


def _level_key(level: float) -> str:
    # Stable alert_state key, e.g. pnl_-40
    if float(level).is_integer():
        return f"pnl_{int(level)}"
    return f"pnl_{level:g}"


def _exit_enabled(kind: str, prefs: AlertPrefs) -> bool:
    if kind == "liq":
        return prefs.liq_enabled
    if kind == "tp":
        return prefs.tp_enabled
    if kind == "sl":
        return prefs.sl_enabled
    # "closed" always allowed when not muted (informational)
    return True


class AlertEngine:
    def __init__(
        self,
        db: Database,
        *,
        cooldown_seconds: float,
        hysteresis_percent: float,
        pnl_alert_levels: tuple[float, ...] | None = None,
        liq_pnl_percent: float | None = None,
    ) -> None:
        self.db = db
        self.cooldown_seconds = cooldown_seconds
        self.hysteresis_percent = hysteresis_percent
        # Global defaults used only when prefs missing / as fallback seed.
        self.pnl_alert_levels = tuple(
            pnl_alert_levels if pnl_alert_levels is not None else PNL_ALERT_LEVELS
        )
        self.liq_pnl_percent = (
            LIQ_PNL_PERCENT if liq_pnl_percent is None else float(liq_pnl_percent)
        )

    async def evaluate(
        self,
        chat_id: int,
        positions: list[Position],
        muted: bool,
    ) -> list[OutgoingAlert]:
        async with self.db.transaction():
            alerts = await self._evaluate(chat_id, positions, muted)
            for alert in alerts:
                await self.db.enqueue_alert(chat_id, alert.text, alert.kind)
            return alerts

    async def _evaluate(self, chat_id, positions, muted):
        alerts: list[OutgoingAlert] = []
        prefs = await self.db.get_alert_prefs(chat_id)
        current_keys = {p.position_key for p in positions}
        prev_map = await self.db.list_position_snapshots(chat_id)

        for key, raw in list(prev_map.items()):
            if key in current_keys:
                continue
            prev = position_from_json(raw)
            if prev.position_key in current_keys:
                await self.db.delete_position_snapshots(chat_id, [key])
                continue
            mark = prev.mark_price
            for p in positions:
                if p.pair_index == prev.pair_index and p.mark_price:
                    mark = p.mark_price
                    break
            kind = classify_exit(prev, mark, liq_pnl_percent=self.liq_pnl_percent)
            if not muted and _exit_enabled(kind, prefs):
                alerts.append(
                    OutgoingAlert(
                        text=format_exit_alert(kind, prev, mark),
                        kind=kind,
                        attach_chart=(kind == "liq"),
                        position=prev,
                    )
                )
            await self.db.delete_position_snapshots(chat_id, [key])

        for pos in positions:
            await self.db.save_position_snapshot(
                chat_id, pos.position_key, position_to_json(pos)
            )
            if muted:
                continue
            pnl_pct = pos.unrealized_pnl_pct
            buffer = pos.buffer_to_liq_pts()
            # Legacy threshold presets correspond to 45/25/10 collateral points
            # from the authoritative liquidation price, not a fixed net loss.
            risk_pct = self.liq_pnl_percent + buffer if buffer is not None else None
            if prefs.liq_enabled and risk_pct is not None:
                for level in prefs.liq_levels:
                    level_name = _level_key(level)
                    fired = await self._maybe_fire_liq_proximity(
                        chat_id, pos, level, risk_pct, level_name
                    )
                    if fired:
                        alerts.append(
                            OutgoingAlert(
                                text=format_liq_alert(
                                    pos,
                                    level,
                                    pnl_pct,
                                    liq_pnl_percent=self.liq_pnl_percent,
                                ),
                                kind=level_name,
                                attach_chart=True,
                                position=pos,
                            )
                        )

            if pnl_pct is None:
                pnl_pct = pnl_pct_at_price(price=pos.mark_price or 0, open_price=pos.open_price,
                    collateral=pos.collateral_usdc, leverage=pos.leverage, is_long=pos.is_long)
            if pnl_pct is None or not math.isfinite(pnl_pct):
                continue
            if prefs.tp_enabled and pos.tp and pos.tp > 0:
                alert = await self._maybe_fire_tp_proximity(chat_id, pos, pnl_pct, prefs)
                if alert:
                    alerts.append(alert)

            if prefs.sl_enabled and pos.sl and pos.sl > 0:
                alert = await self._maybe_fire_sl_proximity(chat_id, pos, pnl_pct, prefs)
                if alert:
                    alerts.append(alert)

        await self.db.clear_alert_states(chat_id, current_keys)
        return alerts

    async def _maybe_fire_liq_proximity(
        self,
        chat_id: int,
        pos: Position,
        level: float,
        pnl_pct: float,
        level_name: str,
    ) -> bool:
        """Fire when unrealized PnL% crosses at/below `level` (e.g. -60).

        Rearm after PnL recovers above level + hysteresis (PnL percentage points).
        """
        last_fired, armed = await self.db.get_alert_state(
            chat_id, pos.position_key, level_name
        )
        now = time.time()

        rearm_line = level + self.hysteresis_percent
        if not armed and pnl_pct > rearm_line:
            await self.db.set_alert_state(
                chat_id, pos.position_key, level_name, armed=True
            )
            armed = True

        # Worse PnL is more negative; only fire at/through the threshold.
        if pnl_pct > level:
            return False
        if not armed:
            return False
        if last_fired and (now - last_fired) < self.cooldown_seconds:
            return False

        await self.db.set_alert_state(
            chat_id,
            pos.position_key,
            level_name,
            last_fired_at=now,
            armed=False,
        )
        return True

    async def _cooldown_ok(self, chat_id: int, position_key: str, level: str) -> bool:
        last_fired, _armed = await self.db.get_alert_state(
            chat_id, position_key, level
        )
        now = time.time()
        if last_fired and (now - last_fired) < self.cooldown_seconds:
            return False
        await self.db.set_alert_state(
            chat_id,
            position_key,
            level,
            last_fired_at=now,
            armed=False,
        )
        return True

    async def _maybe_fire_tp_proximity(
        self,
        chat_id: int,
        pos: Position,
        current_pnl_pct: float,
        prefs: AlertPrefs,
    ) -> OutgoingAlert | None:
        tp_pnl = tp_sl_target_pnl_pct(
            target_price=pos.tp,
            open_price=pos.open_price,
            collateral=pos.collateral_usdc,
            leverage=pos.leverage,
            is_long=pos.is_long,
        )
        if tp_pnl is None:
            return None
        gross_current = pnl_pct_at_price(
            price=pos.mark_price or 0, open_price=pos.open_price,
            collateral=pos.collateral_usdc, leverage=pos.leverage, is_long=pos.is_long,
        )
        if gross_current is None:
            return None
        # Compare both levels on the same net basis, holding current fees constant.
        tp_pnl -= gross_current - current_pnl_pct
        remaining = remaining_to_tp_pnl(current_pnl_pct, tp_pnl)
        if not within_pnl_distance(remaining, prefs.tp_distance_pnl):
            # Rearm when user moves back outside the proximity band.
            if remaining > prefs.tp_distance_pnl:
                await self.db.set_alert_state(
                    chat_id, pos.position_key, TP_PROX_LEVEL, armed=True
                )
            return None

        last_fired, armed = await self.db.get_alert_state(
            chat_id, pos.position_key, TP_PROX_LEVEL
        )
        now = time.time()
        if not armed:
            return None
        if last_fired and (now - last_fired) < self.cooldown_seconds:
            return None

        await self.db.set_alert_state(
            chat_id,
            pos.position_key,
            TP_PROX_LEVEL,
            last_fired_at=now,
            armed=False,
        )
        return OutgoingAlert(
            text=format_proximity_alert(
                kind="tp",
                pos=pos,
                current_pnl_pct=current_pnl_pct,
                target_pnl_pct=tp_pnl,
                remaining_pts=remaining,
                distance_cfg=prefs.tp_distance_pnl,
            ),
            kind=TP_PROX_LEVEL,
            attach_chart=True,
            position=pos,
        )

    async def _maybe_fire_sl_proximity(
        self,
        chat_id: int,
        pos: Position,
        current_pnl_pct: float,
        prefs: AlertPrefs,
    ) -> OutgoingAlert | None:
        sl_pnl = tp_sl_target_pnl_pct(
            target_price=pos.sl,
            open_price=pos.open_price,
            collateral=pos.collateral_usdc,
            leverage=pos.leverage,
            is_long=pos.is_long,
        )
        if sl_pnl is None:
            return None
        gross_current = pnl_pct_at_price(
            price=pos.mark_price or 0, open_price=pos.open_price,
            collateral=pos.collateral_usdc, leverage=pos.leverage, is_long=pos.is_long,
        )
        if gross_current is None:
            return None
        # Compare both levels on the same net basis, holding current fees constant.
        sl_pnl -= gross_current - current_pnl_pct
        remaining = remaining_to_sl_pnl(current_pnl_pct, sl_pnl)
        if not within_pnl_distance(remaining, prefs.sl_distance_pnl):
            if remaining > prefs.sl_distance_pnl:
                await self.db.set_alert_state(
                    chat_id, pos.position_key, SL_PROX_LEVEL, armed=True
                )
            return None

        last_fired, armed = await self.db.get_alert_state(
            chat_id, pos.position_key, SL_PROX_LEVEL
        )
        now = time.time()
        if not armed:
            return None
        if last_fired and (now - last_fired) < self.cooldown_seconds:
            return None

        await self.db.set_alert_state(
            chat_id,
            pos.position_key,
            SL_PROX_LEVEL,
            last_fired_at=now,
            armed=False,
        )
        return OutgoingAlert(
            text=format_proximity_alert(
                kind="sl",
                pos=pos,
                current_pnl_pct=current_pnl_pct,
                target_pnl_pct=sl_pnl,
                remaining_pts=remaining,
                distance_cfg=prefs.sl_distance_pnl,
            ),
            kind=SL_PROX_LEVEL,
            attach_chart=True,
            position=pos,
        )
