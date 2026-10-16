"""Premium fintech position ticket (Pillow). Used when LW candles are unavailable.

1080×720 card with hero PnL, short-interval observed candles and risk stats.
Risk levels never expand the price scale. Missing observations stay missing.
"""

from __future__ import annotations

import io
import logging
import math
import time
from pathlib import Path

from .config import LIQ_PNL_PERCENT
from .formatting import display_pair
from .ticks import load_ticks
from .candles import build_candles, prepare_tape

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = REPO_ROOT / "assets" / "fonts"
DINO_PATH = REPO_ROOT / "assets" / "brand" / "dino.png"

# Logical canvas (rendered at 2× then Lanczos-downsampled).
TICKET_W = 1080
TICKET_H = 720
SCALE = 2

# Palette
_BG = (7, 8, 10)  # #07080A
_HAIR = (26, 29, 36)  # #1A1D24
_PANEL = (12, 13, 16)
_PANEL_TOP = (16, 18, 22)
_TEXT = (236, 238, 243)
_CREAM = (246, 241, 232)  # #F6F1E8
_MUTED = (118, 124, 134)
_LABEL = (88, 94, 104)
_TEAL = (61, 255, 138)  # #3DFF8A
_RED = (255, 77, 77)  # #FF4D4D
_OPEN_LINE = (154, 168, 186)
_WHITE = (255, 255, 255)
_PILL_LONG_BG = (10, 36, 24)
_PILL_SHORT_BG = (42, 14, 16)


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
            _w, frac = s.split(".", 1)
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
        return "—"
    v = float(value)
    if signed:
        sign = "+" if v > 0 else ("\u2212" if v < 0 else "")
    else:
        sign = "\u2212" if v < 0 else ""
    abs_v = abs(v)
    if abs_v >= 100:
        body = f"{abs_v:,.1f}" if abs_v < 1000 else f"{abs_v:,.0f}"
    elif abs_v >= 10:
        body = f"{abs_v:.1f}"
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


def _fmt_buf(pts: float | None) -> str:
    if pts is None:
        return "—"
    v = float(pts)
    if abs(v) >= 10:
        return f"{v:.0f} pts"
    return f"{v:.1f} pts"


def _norm_side(side: str | None, is_long: bool | None) -> tuple[str, bool]:
    if is_long is not None:
        return ("LONG" if is_long else "SHORT", bool(is_long))
    s = (side or "").strip().upper()
    if s.startswith("LONG") or s in ("BUY", "L"):
        return "LONG", True
    return "SHORT", False



def _dino_icon(size: int):
    """Square-cropped dino sticker, or None if the asset is missing."""
    if size <= 0 or not DINO_PATH.is_file():
        return None
    try:
        from PIL import Image

        im = Image.open(DINO_PATH).convert("RGBA")
    except Exception:  # noqa: BLE001
        logger.warning("dino brand asset unreadable: %s", DINO_PATH)
        return None
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    return im.resize((size, size), Image.Resampling.LANCZOS)


def _font_path(name: str) -> Path:
    return FONT_DIR / name


def _load_fonts(scale: int = SCALE):
    from PIL import ImageFont

    def ttf(name: str, size: int):
        path = _font_path(name)
        if not path.is_file():
            raise FileNotFoundError(path)
        return ImageFont.truetype(str(path), size * scale)

    # Fail loud — never silently drop to bitmap default fonts.
    return {
        "wordmark": ttf("Geist-Medium.ttf", 13),
        "pair": ttf("Geist-SemiBold.ttf", 32),
        "pill": ttf("Geist-SemiBold.ttf", 12),
        "kicker": ttf("Geist-Medium.ttf", 11),
        "hero": ttf("GeistMono-Bold.ttf", 72),
        "hero_sm": ttf("GeistMono-Bold.ttf", 58),
        "hero_pct": ttf("GeistMono-SemiBold.ttf", 40),
        "hero_pct_sm": ttf("GeistMono-SemiBold.ttf", 32),
        "stat_label": ttf("Geist-Medium.ttf", 10),
        "stat_value": ttf("GeistMono-Medium.ttf", 18),
        "chip": ttf("GeistMono-Medium.ttf", 11),
        "chip_lab": ttf("Geist-Medium.ttf", 10),
        "axis": ttf("GeistMono-Regular.ttf", 11),
        "footer": ttf("Geist-Medium.ttf", 10),
        "empty": ttf("Geist-Medium.ttf", 12),
    }


def _wh(draw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_tracked(draw, xy, text: str, font, fill, tracking: float) -> float:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def _rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _hline(draw, x0, x1, y, color, width=1):
    draw.line([(x0, y), (x1, y)], fill=color, width=width)


def _dashed_hline(draw, x0, x1, y, color, width=1, dash=10, gap=7):
    x = x0
    while x < x1:
        x2 = min(x + dash, x1)
        draw.line([(x, y), (x2, y)], fill=color, width=width)
        x += dash + gap


def _downsample(pts: list[tuple[float, float]], n: int = 240) -> list[tuple[float, float]]:
    if len(pts) <= n:
        return pts
    if n < 3:
        return [pts[0], pts[-1]]
    step = (len(pts) - 1) / (n - 1)
    out = [pts[int(round(i * step))] for i in range(n - 1)]
    out.append(pts[-1])
    return out


def _candle_width(slot_width: float) -> float:
    """Keep an 18% gutter instead of capping wide time slots at 12px."""
    return slot_width * .82


def _tape_range(prices: list[float]) -> tuple[float, float]:
    lo = min(prices)
    hi = max(prices)
    span = hi - lo
    if span <= 0:
        pad = max(abs(lo) * 0.00002, 1e-9)
        return lo - pad, hi + pad
    pad = span * 0.06
    return lo - pad, hi + pad


def _in_data_range(value: float | None, data_lo: float, data_hi: float) -> bool:
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if v <= 0:
        return False
    # Inclusive of the actual tape min/max (not the visual pad).
    return data_lo <= v <= data_hi


def _y_for(price: float, lo: float, hi: float, y0: float, y1: float) -> float:
    if hi <= lo:
        return (y0 + y1) / 2
    t = (price - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    return y1 - t * (y1 - y0)


def _duration_label(ticks: list[tuple[float, float]]) -> str:
    if len(ticks) < 2:
        return "LIVE"
    span = ticks[-1][0] - ticks[0][0]
    if span < 90:
        return "LIVE"
    if span < 3600:
        m = max(1, int(round(span / 60.0)))
        return f"{m}M TAPE"
    h = span / 3600.0
    if h < 1.95:
        return "1H TAPE"
    return f"{h:.0f}H TAPE"


def render_ticket(
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
    ticks: list[tuple[float, float]] | None = None,
    liq_pnl_percent: float = LIQ_PNL_PERCENT,
    candle_interval_s: int = 300,
    window_s: int = 7200,
    chart_label: str = "LOCAL OBSERVATIONS",
    chart_candles: list[dict] | None = None,
) -> bytes | None:
    if candle_interval_s <= 0 or window_s < candle_interval_s:
        raise ValueError("Window must contain at least one positive candle interval")
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except Exception:  # noqa: BLE001
        logger.warning("Pillow unavailable for ticket render")
        return None

    t0 = time.perf_counter()
    try:
        fonts = _load_fonts(SCALE)
    except Exception:  # noqa: BLE001
        logger.exception("Geist fonts missing/unreadable under %s", FONT_DIR)
        return None

    side_label, _long = _norm_side(side, is_long)
    title = display_pair(pair_label)

    if pnl_pct is None and pnl_usdc is not None and collateral and collateral > 0:
        pnl_pct = float(pnl_usdc) / float(collateral) * 100.0

    if buffer_pts is None and pnl_pct is not None:
        buffer_pts = float(pnl_pct) - float(liq_pnl_percent)

    win = pnl_pct is not None and pnl_pct >= 0
    accent = _TEAL if win else _RED
    if pnl_usdc is None and pnl_pct is None:
        accent = _TEXT
    pnl_color = accent

    if ticks is None and pair_index is not None:
        # Never downsample before OHLC aggregation: that discards highs/lows.
        ticks = load_ticks(int(pair_index), hours=window_s/3600, limit=0)
    ticks = prepare_tape(ticks or [], window=window_s)

    S = SCALE
    W, H = TICKET_W * S, TICKET_H * S
    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    # Outer hairline + top accent.
    draw.rectangle([0, 0, W - 1, H - 1], outline=_HAIR, width=S)
    draw.rectangle([0, 0, W, 3 * S], fill=accent)

    mx = 48 * S
    right = W - mx

    # --- Wordmark ----------------------------------------------------------
    wy = 26 * S
    dino_px = 22 * S
    icon = _dino_icon(dino_px)
    if icon is not None:
        dino_y = 20 * S
        mask = Image.new("L", (dino_px, dino_px), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, dino_px - 1, dino_px - 1],
            radius=6 * S,
            fill=255,
        )
        img.paste(icon.convert("RGB"), (mx, dino_y), mask)
        wx = mx + dino_px + 10 * S
        wy = dino_y + 5 * S
    else:
        led = 7 * S
        draw.rounded_rectangle(
            [mx, wy + 3 * S, mx + led, wy + 3 * S + led],
            radius=2 * S,
            fill=accent,
        )
        wx = mx + led + 10 * S
    end_av = _draw_tracked(
        draw,
        (wx, wy),
        "veranta",
        fonts["wordmark"],
        _CREAM,
        tracking=1.2 * S,
    )
    _draw_tracked(
        draw,
        (end_av + 8 * S, wy),
        "alerts",
        fonts["wordmark"],
        _MUTED,
        tracking=1.2 * S,
    )

    # Side pill (top-right).
    pill_txt = side_label
    pw, ph = _wh(draw, pill_txt, fonts["pill"])
    pad_x, pad_y = 14 * S, 6 * S
    pill_w, pill_h = pw + pad_x * 2, ph + pad_y * 2
    pill_x1 = right
    pill_x0 = pill_x1 - pill_w
    pill_y0 = 22 * S
    if side_label == "LONG":
        pill_bg, pill_fg = _PILL_LONG_BG, _TEAL
    else:
        pill_bg, pill_fg = _PILL_SHORT_BG, _RED
    _rounded(
        draw,
        [pill_x0, pill_y0, pill_x1, pill_y0 + pill_h],
        radius=pill_h // 2,
        fill=pill_bg,
        outline=pill_fg,
        width=S,
    )
    draw.text(
        (pill_x0 + pad_x, pill_y0 + pad_y - S),
        pill_txt,
        fill=pill_fg,
        font=fonts["pill"],
    )

    # Pair name.
    draw.text((mx, 52 * S), title, fill=_TEXT, font=fonts["pair"])

    # --- Hero PnL ----------------------------------------------------------
    _draw_tracked(
        draw,
        (mx, 96 * S),
        "NET PNL",
        fonts["kicker"],
        _LABEL,
        tracking=2.2 * S,
    )

    usd_txt = _fmt_pnl(pnl_usdc) if pnl_usdc is not None else "—"
    pct_txt = _fmt_pct(pnl_pct) if pnl_pct is not None else ""

    hero_font = fonts["hero"]
    pct_font = fonts["hero_pct"]
    hw = _wh(draw, usd_txt, hero_font)[0]
    pct_w_est = _wh(draw, pct_txt or "+00.0%", pct_font)[0] if pct_txt else 0
    if hw > (right - mx) - pct_w_est - 40 * S:
        hero_font = fonts["hero_sm"]
        pct_font = fonts["hero_pct_sm"]

    hero_base = 178 * S
    draw.text((mx, hero_base), usd_txt, fill=pnl_color, font=hero_font, anchor="ls")
    if pct_txt:
        draw.text((right, hero_base), pct_txt, fill=pnl_color, font=pct_font, anchor="rs")

    # Observed candles: scale only to actual prices; risk levels live in the stats.
    sx0, sy0, sx1, sy1 = mx, 206 * S, right, 484 * S
    _rounded(draw, [sx0, sy0, sx1, sy1], 14 * S, fill=_PANEL, outline=_HAIR, width=S)
    interval = candle_interval_s
    candles = build_candles(ticks, interval) if chart_candles is None else chart_candles
    age = max(0, time.time() - ticks[-1][0]) if ticks else 0
    status = 'COLLECTING'
    if candles:
        interval_label = f'{interval}s' if interval < 60 else f'{interval//60}m'
        status = f'{interval_label} CANDLES  ·  {chart_label}'
        if candles[-1]['time'] == int(time.time() // interval)*interval:
            status += '  ·  LAST BAR DEVELOPING'
        if age > 15:
            status += f'  ·  LAST UPDATE {int(age)}s AGO'
        elif len(candles) < 3 or sum(c.get('count', 2) == 1 for c in candles) > len(candles)/2:
            status += '  ·  PARTIAL HISTORY'
        elif all(c['high'] == candles[0]['high'] == c['low'] for c in candles):
            status += '  ·  UNCHANGED'
    draw.text((sx0 + 18*S, sy0 + 14*S), status, fill=_MUTED, font=fonts['footer'])
    plot_x0, plot_x1 = sx0 + 22*S, sx1 - 118*S
    plot_y0, plot_y1 = sy0 + 50*S, sy1 - 36*S
    if candles:
        prices = [p for c in candles for p in (c['low'], c['high'])]
        lo, hi = _tape_range(prices)
        py = lambda p: _y_for(p, lo, hi, plot_y0, plot_y1)
        decimals = min(10, max(2, int(math.ceil(-math.log10((hi-lo)/3))) + 1))
        # Keep the requested time window; sparse history stays at its actual time.
        end = int(time.time() // interval) * interval + interval
        start = min(candles[0]['time'], end - window_s)
        span = end - start
        px = lambda ts: plot_x0 + (ts - start) / span * (plot_x1 - plot_x0)
        for i in range(4):
            price = lo + (hi-lo) * i/3
            y = py(price)
            draw.line([(plot_x0, y), (plot_x1, y)], fill=_HAIR, width=S)
            draw.text((plot_x1 + 12*S, y), f'${price:,.{decimals}f}', fill=_MUTED,
                      font=fonts['axis'], anchor='lm')
        for i in range(4):
            ts = start + span*i/3
            x = px(ts)
            label = time.strftime('%H:%M:%S' if interval < 60 else '%H:%M', time.gmtime(ts))
            draw.text((x, plot_y1 + 14*S), label, fill=_MUTED,
                      font=fonts['footer'], anchor='mt')
        width = _candle_width((plot_x1-plot_x0) * interval/span)
        for c in candles:
            x = px(c['time'] + interval/2)
            color = _TEAL if c['close'] >= c['open'] else _RED
            if c.get('count', 2) == 1:
                y = py(c['close'])
                draw.ellipse([x-2*S, y-2*S, x+2*S, y+2*S], fill=_MUTED)
                continue
            draw.line([(x, py(c['high'])), (x, py(c['low']))], fill=color, width=S)
            top, bottom = sorted([py(c['open']), py(c['close'])])
            if bottom-top < 2*S:
                draw.line([(x-width/2, top), (x+width/2, top)], fill=color, width=2*S)
            else:
                draw.rectangle([x-width/2, top, x+width/2, bottom], fill=color)
        # Subtle level hairlines stay behind the candle detail in visual weight.
        for price, color in [(open_price, _OPEN_LINE), (liquidation, _RED),
                             (tp, _TEAL), (sl, (255,171,64))]:
            if price and lo <= price <= hi:
                _dashed_hline(draw, plot_x0, plot_x1, py(price), color, width=S, dash=4*S, gap=8*S)
        draw.text((sx1-18*S, sy1-16*S), 'UTC', fill=_LABEL, font=fonts['footer'], anchor='rs')
    else:
        draw.text(((sx0+sx1)/2, (sy0+sy1)/2-10*S), 'Waiting for recent price observations',
                  fill=_TEXT, font=fonts['empty'], anchor='mm')
        draw.text(((sx0+sx1)/2, (sy0+sy1)/2+18*S), 'Candles appear as the live feed arrives',
                  fill=_MUTED, font=fonts['footer'], anchor='mm')

    # --- Stats grid --------------------------------------------------------
    stats_top = 506 * S
    _hline(draw, mx, right, stats_top, _HAIR, width=S)

    stats: list[tuple[str, str]] = [
        ("MARK", _fmt_price(mark)),
        ("ENTRY", _fmt_price(open_price)),
        ("LIQ", _fmt_price(liquidation)),
        ("TP", _fmt_price(tp) if tp and tp > 0 else "\u2014"),
        ("SL", _fmt_price(sl) if sl and sl > 0 else "\u2014"),
        ("LEV", _fmt_lev(leverage)),
        ("COLL", _fmt_coll(collateral)),
        ("BUFFER", _fmt_buf(buffer_pts)),
    ]

    cols = 4
    inner = right - mx
    col_w = inner / cols
    row_h = 78 * S
    grid_y = stats_top + 16 * S

    for i, (lab, val) in enumerate(stats):
        rr, c = divmod(i, cols)
        cx0 = mx + c * col_w
        cy0 = grid_y + rr * row_h
        if c > 0:
            draw.line(
                [(cx0, cy0 + 2 * S), (cx0, cy0 + row_h - 14 * S)],
                fill=_HAIR,
                width=S,
            )
        if rr == 1 and c == 0:
            _hline(draw, mx, right, cy0 - 6 * S, _HAIR, width=S)
        if candles and lab in ('ENTRY', 'LIQ', 'TP', 'SL'):
            level = {'ENTRY': open_price, 'LIQ': liquidation, 'TP': tp, 'SL': sl}[lab]
            if level and (level < lo or level > hi):
                lab += ' ↑' if level > hi else ' ↓'
        _draw_tracked(
            draw,
            (cx0 + 16 * S, cy0 + 4 * S),
            lab,
            fonts["stat_label"],
            _LABEL,
            tracking=2.2 * S,
        )
        draw.text(
            (cx0 + 16 * S, cy0 + 24 * S),
            val,
            fill=_TEXT,
            font=fonts["stat_value"],
        )

    fy = H - 26 * S
    _hline(draw, mx, right, fy - 10 * S, _HAIR, width=S)
    pair_s = f"PAIR {pair_index}" if pair_index is not None else "VERANTA"
    foot = f"{pair_s}   ·   VERANTA LAZER"
    _draw_tracked(draw, (mx, fy), foot, fonts["footer"], _LABEL, tracking=1.8 * S)

    # Downsample 2× → 1× for crisp type + lines.
    out = img.resize((TICKET_W, TICKET_H), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    png = buf.getvalue()
    ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "ticket: %d bytes in %.0fms title=%r side=%s ticks=%d pnl_pct=%s",
        len(png),
        ms,
        title,
        side_label,
        len(ticks),
        None if pnl_pct is None else round(pnl_pct, 2),
    )
    return png
