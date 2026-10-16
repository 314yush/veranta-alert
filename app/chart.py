"""Alert chart renderer: TradingView lightweight-charts PNG + Pillow ticket.

Primary path (mapped crypto with OHLC):
  Binance/OKX/Coinbase candles → Node headless LW charts → PNG
  Overlays: open / mark / liq / TP / SL

Fallback (unmapped / exotic pairs — XRP_UPSIDE, XAU, FX — or renderer failure):
  Premium Pillow *ticket* (Geist, hero PnL, Lazer sparkline, stats grid).
  Never maps XRP_UPSIDE to XRPUSDT candles.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from .config import LIQ_PNL_PERCENT
from .formatting import display_pair
from .ohlc import fetch_candles, is_exotic_pair
from .prefs import pnl_pct_at_price
from .ticket import render_ticket

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CHARTS_DIR = REPO_ROOT / "charts"
RENDER_JS = CHARTS_DIR / "render.mjs"
NODE_BIN = os.environ.get("NODE_BIN", "node")

# LW LineStyle: 0 Solid, 1 Dotted, 2 Dashed, 3 LargeDashed
LINE_SOLID = 0
LINE_DOTTED = 1
LINE_DASHED = 2

CHART_WIDTH = 900
CHART_HEIGHT = 480
RISK_WIDTH = 900
RISK_HEIGHT = 520
RENDER_TIMEOUT_S = 12.0

_FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Palette
_BG = (10, 11, 13)  # #0A0B0D
_BORDER = (32, 36, 44)
_TEXT = (242, 244, 247)
_MUTED = (138, 144, 154)
_LABEL = (104, 110, 120)
_RED = (255, 82, 82)
_GREEN = (61, 220, 151)
_TRACK = (22, 25, 32)
_DANGER_ZONE = (52, 20, 26)
_PROFIT_ZONE = (16, 40, 32)
_HAIRLINE = (28, 32, 40)
_PILL_LONG_BG = (16, 46, 36)
_PILL_LONG_FG = (61, 220, 151)
_PILL_SHORT_BG = (56, 20, 24)
_PILL_SHORT_FG = (255, 107, 122)


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v <= 0:
        return "—"
    abs_v = abs(v)
    if abs_v >= 1000:
        return f"${v:,.2f}"
    if abs_v >= 10:
        return f"${v:.2f}"
    if abs_v >= 1:
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        if "." not in s:
            s = f"{s}.00"
        else:
            whole, frac = s.split(".", 1)
            if len(frac) < 2:
                s = f"{v:.2f}"
        return f"${s}"
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    if "." not in s:
        s = f"{s}.00"
    return f"${s}"


def _fmt_pnl(value: float) -> str:
    sign = "+" if value >= 0 else "\u2212"
    abs_v = abs(value)
    body = f"{abs_v:,.2f}" if abs_v >= 1000 else f"{abs_v:.2f}"
    return f"{sign}${body}"


def _fmt_pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "n/a"
    v = float(value)
    if signed:
        sign = "+" if v > 0 else ("\u2212" if v < 0 else "")
    else:
        sign = "\u2212" if v < 0 else ""
    abs_v = abs(v)
    if abs_v >= 100:
        body = f"{abs_v:,.0f}"
    elif abs_v >= 10:
        body = f"{abs_v:.0f}"
    else:
        body = f"{abs_v:.1f}"
    return f"{sign}{body}%"


def _fmt_lev(value: float | None) -> str:
    if value is None or value <= 0:
        return "—"
    s = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return f"{s}\u00d7"


def _fmt_coll(value: float | None) -> str:
    if value is None or value <= 0:
        return "—"
    v = float(value)
    if abs(v) >= 1000:
        s = f"${v:,.2f}"
    else:
        s = f"${v:.2f}"
    if s.endswith(".00"):
        s = s[:-3]
    return s


def _norm_side(side: str | None, is_long: bool | None) -> tuple[str, bool]:
    if is_long is not None:
        return ("LONG" if is_long else "SHORT", bool(is_long))
    s = (side or "").strip().upper()
    if s.startswith("LONG") or s in ("BUY", "L"):
        return "LONG", True
    return "SHORT", False


def _build_lines(
    *,
    mark: float | None,
    open_price: float | None,
    liquidation: float | None,
    tp: float | None,
    sl: float | None,
    pnl_usdc: float | None,
) -> list[dict]:
    lines: list[dict] = []
    if open_price and open_price > 0:
        lines.append(
            {
                "price": float(open_price),
                "title": f"Open {_fmt_price(open_price)}",
                "color": "#6ec6ff",
                "lineStyle": LINE_DASHED,
                "lineWidth": 2,
            }
        )
    if mark and mark > 0:
        title = f"Mark {_fmt_price(mark)}"
        if pnl_usdc is not None:
            title = f"Mark {_fmt_price(mark)} (PnL {_fmt_pnl(pnl_usdc)})"
        lines.append(
            {
                "price": float(mark),
                "title": title,
                "color": "#ffffff",
                "lineStyle": LINE_SOLID,
                "lineWidth": 2,
            }
        )
    if liquidation and liquidation > 0:
        lines.append(
            {
                "price": float(liquidation),
                "title": f"Liq {_fmt_price(liquidation)}",
                "color": "#ff5252",
                "lineStyle": LINE_SOLID,
                "lineWidth": 2,
            }
        )
    if sl and sl > 0:
        lines.append(
            {
                "price": float(sl),
                "title": f"SL {_fmt_price(sl)}",
                "color": "#ffab40",
                "lineStyle": LINE_DOTTED,
                "lineWidth": 2,
            }
        )
    if tp and tp > 0:
        lines.append(
            {
                "price": float(tp),
                "title": f"TP {_fmt_price(tp)}",
                "color": "#69f0ae",
                "lineStyle": LINE_DOTTED,
                "lineWidth": 2,
            }
        )
    return lines


def render_lw_chart(
    *,
    candles: list[dict],
    lines: list[dict],
    title: str,
    width: int = CHART_WIDTH,
    height: int = CHART_HEIGHT,
) -> bytes | None:
    """Invoke Node LW renderer; return PNG bytes or None on failure."""
    if not RENDER_JS.is_file():
        logger.warning("LW renderer missing: %s", RENDER_JS)
        return None
    if not candles:
        return None

    payload = {
        "candles": candles,
        "lines": lines,
        "title": title,
        "width": width,
        "height": height,
    }

    t0 = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="veranta-lw-") as tmp:
            tmp_path = Path(tmp)
            in_path = tmp_path / "in.json"
            out_path = tmp_path / "out.png"
            in_path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [NODE_BIN, str(RENDER_JS), str(in_path), str(out_path)],
                capture_output=True,
                timeout=RENDER_TIMEOUT_S,
                check=False,
            )
            if proc.returncode != 0 or not out_path.is_file():
                err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")
                logger.warning(
                    "LW renderer failed rc=%s: %s",
                    proc.returncode,
                    err[-500:],
                )
                return None
            png = out_path.read_bytes()
    except subprocess.TimeoutExpired:
        logger.warning("LW renderer timed out after %.1fs", RENDER_TIMEOUT_S)
        return None
    except Exception:  # noqa: BLE001
        logger.exception("LW renderer invocation failed")
        return None

    ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "chart lw: %d candles, %d lines, %d bytes in %.0fms title=%r",
        len(candles),
        len(lines),
        len(png),
        ms,
        title,
    )
    return png if png else None


def _pillow_available() -> bool:
    try:
        from PIL import Image  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _load_fonts():
    from PIL import ImageFont

    def _try(path: str, size: int):
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            return None

    default = ImageFont.load_default()

    def pick(bold: bool, size: int):
        f = _try(_FONT_BOLD if bold else _FONT_REG, size)
        return f or default

    return {
        "title": pick(True, 32),
        "pill": pick(True, 16),
        "hero": pick(True, 56),
        "hero_pct": pick(True, 44),
        "kicker": pick(False, 13),
        "meter": pick(False, 13),
        "tick": pick(False, 13),
        "stat_label": pick(True, 11),
        "stat_value": pick(False, 16),
        "now": pick(True, 13),
    }


def _text_wh(draw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _target_pnl_pct(
    *,
    price: float | None,
    open_price: float | None,
    leverage: float | None,
    is_long: bool,
    collateral: float | None,
) -> float | None:
    if not price or price <= 0 or not open_price or open_price <= 0:
        return None
    if not leverage or leverage <= 0:
        return None
    coll = collateral if collateral and collateral > 0 else 1.0
    return pnl_pct_at_price(
        price=float(price),
        open_price=float(open_price),
        collateral=float(coll),
        leverage=float(leverage),
        is_long=is_long,
    )


# Visual right-bound cap so a moonshot TP (e.g. +2,400%) does not
# crush current PnL against the left edge. The TP *label* still shows
# the true TP PnL%; the tick sits at the right end of the meter.
_TP_VISUAL_CAP = 100.0


def _meter_bounds(
    *,
    pnl_pct: float | None,
    tp_pnl: float | None,
    liq_pnl: float,
) -> tuple[float, float]:
    left = float(liq_pnl)
    right = 0.0
    if tp_pnl is not None:
        tp = float(tp_pnl)
        right = max(right, tp if tp <= _TP_VISUAL_CAP else _TP_VISUAL_CAP)
    if pnl_pct is not None:
        right = max(right, float(pnl_pct), 0.0)
        left = min(left, float(pnl_pct))
    if right - left < 8.0:
        right = left + 8.0
    return left, right


def _meter_x(
    pct: float,
    *,
    left: float,
    right: float,
    x0: float,
    x1: float,
) -> float:
    """Map PnL% → x on a linear liq → right-bound scale (clamped)."""
    if x1 <= x0:
        return x0
    span = right - left
    t = (pct - left) / span if span else 0.0
    t = max(0.0, min(1.0, t))
    return x0 + t * (x1 - x0)


def render_risk_card(
    *,
    pair_label: str,
    side: str,
    mark: float | None,
    open_price: float | None,
    liquidation: float | None,
    tp: float | None = None,
    sl: float | None = None,
    pnl_usdc: float | None = None,
    pnl_pct: float | None = None,
    collateral: float | None = None,
    leverage: float | None = None,
    is_long: bool | None = None,
    liq_pnl_percent: float = LIQ_PNL_PERCENT,
    pair_index: int | None = None,
    buffer_pts: float | None = None,
    ticks: list | None = None,
) -> bytes | None:
    """Premium fintech ticket (Pillow). Sparkline uses Lazer ticks when present."""
    return render_ticket(
        pair_label=pair_label,
        side=side,
        mark=mark,
        open_price=open_price,
        liquidation=liquidation,
        tp=tp,
        sl=sl,
        pnl_usdc=pnl_usdc,
        pnl_pct=pnl_pct,
        collateral=collateral,
        leverage=leverage,
        is_long=is_long,
        liq_pnl_percent=liq_pnl_percent,
        pair_index=pair_index,
        buffer_pts=buffer_pts,
        ticks=ticks,
    )


def render_levels_card(
    *,
    pair_label: str,
    side: str,
    mark: float | None,
    open_price: float | None,
    liquidation: float | None,
    tp: float | None = None,
    sl: float | None = None,
    pnl_usdc: float | None = None,
    pnl_pct: float | None = None,
    collateral: float | None = None,
    leverage: float | None = None,
    is_long: bool | None = None,
) -> bytes | None:
    """Back-compat alias for the Pillow risk card (no empty price axes)."""
    return render_risk_card(
        pair_label=pair_label,
        side=side,
        mark=mark,
        open_price=open_price,
        liquidation=liquidation,
        tp=tp,
        sl=sl,
        pnl_usdc=pnl_usdc,
        pnl_pct=pnl_pct,
        collateral=collateral,
        leverage=leverage,
        is_long=is_long,
        pair_index=None,
        buffer_pts=None,
    )


def render_levels_chart(
    *,
    pair_label: str,
    side: str,
    mark: float | None,
    open_price: float | None,
    liquidation: float | None,
    tp: float | None = None,
    sl: float | None = None,
    pnl_usdc: float | None = None,
    pnl_pct: float | None = None,
    collateral: float | None = None,
    leverage: float | None = None,
    is_long: bool | None = None,
    pair_index: int | None = None,
    buffer_pts: float | None = None,
    ticks: list | None = None,
) -> bytes | None:
    """Candles+overlays when mapped+fetched; otherwise the Pillow ticket."""
    t0 = time.perf_counter()
    pretty = display_pair(pair_label)
    title = f"{pretty}  {side}".strip()
    lines = _build_lines(
        mark=mark,
        open_price=open_price,
        liquidation=liquidation,
        tp=tp,
        sl=sl,
        pnl_usdc=pnl_usdc,
    )

    candles = None if (not RENDER_JS.is_file() or is_exotic_pair(pair_label)) else fetch_candles(pair_label)
    if candles:
        png = render_lw_chart(candles=candles, lines=lines, title=title)
        if png:
            logger.info(
                "chart: primary LW ok in %.0fms for %s",
                (time.perf_counter() - t0) * 1000,
                title,
            )
            return png
        logger.warning("chart: LW failed for %s; falling back to risk card", title)
    else:
        logger.info(
            "chart: no OHLC for %s; Pillow risk-card fallback", pair_label
        )

    png = render_risk_card(
        pair_label=pair_label,
        side=side,
        mark=mark,
        open_price=open_price,
        liquidation=liquidation,
        tp=tp,
        sl=sl,
        pnl_usdc=pnl_usdc,
        pnl_pct=pnl_pct,
        collateral=collateral,
        leverage=leverage,
        is_long=is_long,
        pair_index=pair_index,
        buffer_pts=buffer_pts,
        ticks=ticks,
    )
    if png:
        logger.info(
            "chart: fallback risk-card ok in %.0fms for %s",
            (time.perf_counter() - t0) * 1000,
            title,
        )
    return png


def chart_from_position(pos, *, pnl_usdc: float | None = None) -> bytes | None:
    """Choose LW candles if mapped+fetched, else the designed ticket."""
    if pnl_usdc is None:
        pnl_usdc = getattr(pos, "unrealized_pnl_usdc", None)
    buffer_pts = None
    try:
        buffer_pts = pos.buffer_to_liq_pts()
    except Exception:  # noqa: BLE001
        buffer_pts = None
    return render_levels_chart(
        pair_label=getattr(pos, "symbol", "PAIR"),
        side=getattr(pos, "side", ""),
        mark=getattr(pos, "mark_price", None),
        open_price=getattr(pos, "open_price", None),
        liquidation=getattr(pos, "liquidation_price", None),
        tp=getattr(pos, "tp", None) or None,
        sl=getattr(pos, "sl", None) or None,
        pnl_usdc=pnl_usdc,
        pnl_pct=getattr(pos, "unrealized_pnl_pct", None),
        collateral=getattr(pos, "collateral_usdc", None),
        leverage=getattr(pos, "leverage", None),
        is_long=getattr(pos, "is_long", None),
        pair_index=getattr(pos, "pair_index", None),
        buffer_pts=buffer_pts,
    )
