"""Telegram message formatting helpers."""

from __future__ import annotations

import html as html_lib

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.constants import ParseMode

from .veranta_sdk import Position
from .config import LIQ_PNL_PERCENT, PNL_ALERT_LEVELS
from .prefs import (
    LIQ_PRESETS,
    AlertPrefs,
    format_liq_levels,
    normalize_liq_levels,
)

# Telegram hard limit; leave headroom for mute notes / truncation footer.
_TELEGRAM_MSG_LIMIT = 3900
_TELEGRAM_CAPTION_LIMIT = 1024
_MAX_POSITIONS_SHOWN = 12

# Homepage URL is *only* used on the Open Veranta inline button — never in
# caption/alert body text (Telegram would auto-link and show a preview).
VERANTA_URL = "https://veranta.xyz"


def _h(text: object) -> str:
    """Escape dynamic text for Telegram HTML parse_mode."""
    return html_lib.escape(str(text), quote=False)


def html_send_kwargs(**extra: object) -> dict:
    """parse_mode=HTML + homepage preview disabled (text messages).

    Use *only* link_preview_options — PTB treats disable_web_page_preview as
    mutually exclusive. Captions/alerts must never include veranta.xyz;
    Open Veranta is an inline URL button, not a text link.
    """
    kw: dict = {
        "parse_mode": ParseMode.HTML,
        "link_preview_options": LinkPreviewOptions(is_disabled=True),
    }
    kw.update(extra)
    return kw


def html_photo_kwargs(**extra: object) -> dict:
    """parse_mode=HTML for photo captions (no markdown underscores)."""
    kw: dict = {"parse_mode": ParseMode.HTML}
    kw.update(extra)
    return kw


def display_pair(symbol: str) -> str:
    """Human pair label for Telegram.

    Internal symbols stay unchanged (e.g. ``XRP_UPSIDE/USD`` for OHLC/keys).
    Display replaces ``_`` with space, drops a trailing ``/USD`` quote, and
    title-cases long ALL-CAPS tokens: ``XRP_UPSIDE/USD`` → ``XRP Upside``.
    """
    raw = (symbol or "").strip()
    if not raw:
        return "PAIR"
    head = raw.split("/", 1)[0]
    parts = [p for p in head.replace("-", "_").split("_") if p]
    pretty: list[str] = []
    for part in parts:
        if part.isupper() and len(part) > 4:
            pretty.append(part.capitalize())
        else:
            pretty.append(part)
    return " ".join(pretty) if pretty else head


def fit_caption(text: str, limit: int = _TELEGRAM_CAPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _money(value: float | None, digits: int = 2) -> str:
    """Format USD amounts/prices with magnitude-aware precision.

    Near-1.0 marks (e.g. XRP_UPSIDE ≈ 1.0046 vs open 1.0065) must not both
    collapse to `$1.00`/`$1.01` or open/mark look identical in cards/alerts.
    """
    if value is None:
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    abs_v = abs(v)
    if abs_v >= 1000:
        return f"${v:,.{digits}f}"
    if abs_v >= 10:
        return f"${v:.{digits}f}"
    if abs_v >= 1:
        # Keep enough decimals to distinguish tiny mark↔open gaps.
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


def _price(value: float | None) -> str:
    """Compact price without a leading dollar sign (caption levels line)."""
    s = _money(value)
    return s[1:] if s.startswith("$") else s


def _size_money(value: float | None) -> str:
    """Collateral like $10 instead of $10.00 when whole dollars."""
    s = _money(value)
    if s.endswith(".00"):
        return s[:-3]
    return s


def _signed_money(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else "-"
    abs_v = abs(value)
    if abs_v >= 1000:
        body = f"{abs_v:,.{digits}f}"
    else:
        body = f"{abs_v:.{digits}f}"
    return f"{sign}${body}"


def _pnl_pct_label(pnl_pct: float | None) -> str:
    if pnl_pct is None:
        return "n/a"
    sign = "+" if pnl_pct >= 0 else ""
    if abs(pnl_pct) >= 10:
        return f"{sign}{pnl_pct:.0f}%"
    return f"{sign}{pnl_pct:.1f}%"


def _pnl_combo(pos: Position) -> str:
    """e.g. -$0.61 (~-1.2%) — net USDC and % of collateral."""
    return (
        f"{_signed_money(pos.unrealized_pnl_usdc)} "
        f"(~{_pnl_pct_label(pos.unrealized_pnl_pct)})"
    )


def short_wallet(wallet: str) -> str:
    w = (wallet or "").strip()
    if len(w) <= 12:
        return w
    return f"{w[:6]}…{w[-4:]}"


def alert_keyboard() -> InlineKeyboardMarkup:
    """Inline actions on proximity / exit alerts."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Positions", callback_data="positions"),
                InlineKeyboardButton("Mute", callback_data="mute"),
            ],
            [
                InlineKeyboardButton("Settings", callback_data="settings"),
                InlineKeyboardButton("Open Veranta", url=VERANTA_URL),
            ],
        ]
    )


def positions_keyboard(positions=None, page=0):
    positions = positions or []
    rows = []
    for pos in positions[page*8:(page+1)*8]:
        rows.append([InlineKeyboardButton(
            f"{display_pair(pos.symbol)} {pos.side} · Chart",
            callback_data=f"chart:{pos.chart_key}",
        )])
    nav = []
    if page:
        nav.append(InlineKeyboardButton("Previous", callback_data=f"positions:{page-1}"))
    if len(positions) > (page+1)*8:
        nav.append(InlineKeyboardButton("Next", callback_data=f"positions:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("Refresh", callback_data="positions"),
                 InlineKeyboardButton("Settings", callback_data="settings"),
                 InlineKeyboardButton("Open Veranta", url=VERANTA_URL)])
    return InlineKeyboardMarkup(rows)


def _on_off(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


def _toggle_label(name: str, enabled: bool) -> str:
    mark = "✅" if enabled else "⬜"
    return f"{mark} {name}"


def _preset_name_for_levels(levels: tuple[float, ...]) -> str | None:
    norm = normalize_liq_levels(levels)
    for name, preset in LIQ_PRESETS.items():
        if normalize_liq_levels(preset) == norm:
            return name
    return None


def format_settings(prefs: AlertPrefs, *, muted: bool = False) -> str:
    levels_s = "/".join(f"{lv-LIQ_PNL_PERCENT:g}" for lv in prefs.liq_levels)
    preset = _preset_name_for_levels(prefs.liq_levels)
    preset_s = f" ({_h(preset)})" if preset else ""
    mute_note = (
        "\n\n⏸ Global /mute is active — all alerts paused." if muted else ""
    )
    return (
        "⚙️ <b>Alert settings</b>\n"
        f"• Liquidation: <b>{_on_off(prefs.liq_enabled)}</b> · "
        f"buffer <code>{_h(levels_s)}</code> pts{preset_s}\n"
        f"• Take-profit proximity: <b>{_on_off(prefs.tp_enabled)}</b> · "
        f"within <code>{prefs.tp_distance_pnl:g}</code> PnL pts\n"
        f"• Stop-loss proximity: <b>{_on_off(prefs.sl_enabled)}</b> · "
        f"within <code>{prefs.sl_distance_pnl:g}</code> PnL pts\n\n"
        "Distances are <b>Collateral-risk points</b> (leverage-normalized), "
        "from the actual trigger price, not raw price%.\n\n"
        "Tap buttons below, or:\n"
        "<code>/settings tp 15</code> · <code>/settings sl 5</code> · "
        "<code>/settings liq safe|default|tight</code>"
        f"{mute_note}"
    )


def settings_keyboard(prefs: AlertPrefs) -> InlineKeyboardMarkup:
    """Inline controls for /settings (mobile-friendly)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _toggle_label("Liq", prefs.liq_enabled),
                    callback_data="set:toggle:liq",
                ),
                InlineKeyboardButton(
                    _toggle_label("TP", prefs.tp_enabled),
                    callback_data="set:toggle:tp",
                ),
                InlineKeyboardButton(
                    _toggle_label("SL", prefs.sl_enabled),
                    callback_data="set:toggle:sl",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Liq: Safe", callback_data="set:liq:safe"
                ),
                InlineKeyboardButton(
                    "Default", callback_data="set:liq:default"
                ),
                InlineKeyboardButton(
                    "Tight", callback_data="set:liq:tight"
                ),
            ],
            [
                InlineKeyboardButton(
                    "TP dist 5", callback_data="set:tpdist:5"
                ),
                InlineKeyboardButton(
                    "10", callback_data="set:tpdist:10"
                ),
                InlineKeyboardButton(
                    "20", callback_data="set:tpdist:20"
                ),
            ],
            [
                InlineKeyboardButton(
                    "SL dist 5", callback_data="set:sldist:5"
                ),
                InlineKeyboardButton(
                    "10", callback_data="set:sldist:10"
                ),
                InlineKeyboardButton(
                    "20", callback_data="set:sldist:20"
                ),
            ],
            [
                InlineKeyboardButton(
                    "↻ Cycle liq preset", callback_data="set:liq:cycle"
                ),
            ],
        ]
    )


def format_position_card(pos: Position) -> str:
    """Compact HTML per-position caption for /positions photos."""
    buf = pos.buffer_to_liq_pts(liq_pnl_percent=LIQ_PNL_PERCENT)
    buf_s = f"{buf:.0f} pts to liq" if buf is not None else "liq n/a"
    lev = f"{pos.leverage:.2f}".rstrip("0").rstrip(".")
    extras: list[str] = []
    if pos.tp:
        extras.append(f"TP {_h(_price(pos.tp))}")
    if pos.sl:
        extras.append(f"SL {_h(_price(pos.sl))}")
    extra_s = (" · " + " · ".join(extras)) if extras else ""
    name = display_pair(pos.symbol)
    return (
        f"<b>{_h(name)} {_h(pos.side)}</b>\n"
        f"PnL {_h(_pnl_combo(pos))} · {_h(buf_s)}\n"
        f"mark {_h(_price(pos.mark_price))} · open {_h(_price(pos.open_price))} · "
        f"liq {_h(_price(pos.liquidation_price))}\n"
        f"{_h(_size_money(pos.collateral_usdc))} @ {_h(lev)}x{extra_s}"
    )


def format_positions(positions: list[Position], wallet: str, page=0) -> str:
    if not positions:
        return "<b>No open positions.</b>\nI’m still watching this wallet. I’ll check for new trades automatically."
    known = [p.unrealized_pnl_usdc for p in positions if p.unrealized_pnl_usdc is not None]
    total = _signed_money(sum(known)) if len(known) == len(positions) else "unavailable (missing data)"
    lines = [f"<b>{len(positions)} open · Net PnL {total}</b>"]
    for pos in positions[page*8:(page+1)*8]:
        distance = pos.distance_to_liq_pct()
        risk = f"Liq {max(0,distance):.2f}% away" if distance is not None else "Liq distance unavailable"
        stops = f"TP {_price(pos.tp) if pos.tp else 'not set'} · SL {_price(pos.sl) if pos.sl else 'not set'}"
        lines.append(f"<b>{_h(display_pair(pos.symbol))} {pos.side}</b> · {_h(_pnl_combo(pos))}\n{risk} · {stops}")
    lines.append("Tap a position for its chart and price levels.")
    return "\n\n".join(lines)


def format_top_position_summary(pos: Position) -> str:
    """One-line summary for subscribe confirmation."""
    return (
        f"{_h(display_pair(pos.symbol))} {_h(pos.side)} · "
        f"PnL {_h(_pnl_combo(pos))}"
    )


def pick_closest_to_liq(positions: list[Position]) -> Position | None:
    """Position with lowest buffer to liq (most negative pnl% on tie)."""
    if not positions:
        return None

    def sort_key(p: Position) -> tuple[float, float]:
        buf = p.buffer_to_liq_pts(liq_pnl_percent=LIQ_PNL_PERCENT)
        pnl = p.unrealized_pnl_pct
        return (
            buf if buf is not None else 1e9,
            pnl if pnl is not None else 0.0,
        )

    return min(positions, key=sort_key)


def preview_alert_level(pos: Position, levels: tuple[float, ...] | None = None) -> float:
    """Level to style a /test preview: most severe crossed, else mildest."""
    pnl_pct = pos.unrealized_pnl_pct
    use = levels if levels else (PNL_ALERT_LEVELS or (-40.0, -60.0, -75.0))
    chosen = use[0]
    if pnl_pct is None:
        return float(chosen)
    for level in use:
        if pnl_pct <= level:
            chosen = level
    return float(chosen)


def format_liq_alert(
    pos: Position,
    level: float,
    pnl_pct: float,
    *,
    liq_pnl_percent: float = LIQ_PNL_PERCENT,
    preview: bool = False,
) -> str:
    buffer = pos.buffer_to_liq_pts()
    distance = f"{buffer:.1f} collateral pts" if buffer is not None else "unavailable"
    prefix = "Preview — not a real alert\n" if preview else ""
    return (
        f"{prefix}⚠️ <b>{_h(display_pair(pos.symbol))} {pos.side}</b> · liquidation risk\n"
        f"Liq {_price(pos.liquidation_price)} · Mark {_price(pos.mark_price)}\n"
        f"Buffer {distance} · Net PnL {_h(_pnl_combo(pos))}"
    )


def format_proximity_alert(
    *,
    kind: str,
    pos: Position,
    current_pnl_pct: float,
    target_pnl_pct: float,
    remaining_pts: float,
    distance_cfg: float,
) -> str:
    """TP / SL proximity copy (distinct from liquidation alerts)."""
    if kind == "tp":
        emoji = "🎯"
        label = "approaching take-profit"
        target_price = pos.tp
        target_name = "TP"
    else:
        emoji = "🛑"
        label = "approaching stop-loss"
        target_price = pos.sl
        target_name = "SL"
    name = display_pair(pos.symbol)
    return (
        f"{emoji} <b>{_h(name)} {pos.side}</b> · near {target_name}\n"
        f"{target_name} {_price(target_price)} · Mark {_price(pos.mark_price)}\n"
        f"{remaining_pts:.1f} collateral pts away · Net PnL {_h(_pnl_combo(pos))}"
    )


def format_exit_alert(kind: str, pos: Position, mark: float | None) -> str:
    return f"<b>{_h(display_pair(pos.symbol))} {pos.side} closed.</b>\nFinal PnL and close reason are not confirmed yet."


HELP_TEXT = """\
<b>Veranta</b> (Base perps) watch-only alerts.

<b>Commands</b>
<code>/start</code> — intro
<code>/help</code> — this message
<code>/positions</code> — compact open-position cards
<code>/test</code> — preview near-liq alert + chart (no state change)
<code>/settings</code> — toggle Liq / TP / SL + distance presets
<code>/mute</code> — pause all alerts
<code>/unmute</code> — resume alerts
<code>/stop</code> — unsubscribe

Or send a Base wallet (<code>0x</code> + 40 hex) to start watching.

<b>Alerts</b> (Collateral-risk points — leverage-normalized, not raw price%):
• Liquidation warnings within 45 / 25 / 10 collateral points of the API liquidation price
• TP proximity when within N PnL pts of take-profit (default 10)
• SL proximity when within N PnL pts of stop-loss (default 10)
• Closed-position notices; final PnL and reason require confirmed history
• /positions: one summary; tap a position for entry / mark / liq / TP / SL chart
• Buttons: Positions · Mute · Settings · Open Veranta

<code>/mute</code> pauses everything; <code>/settings</code> toggles are finer-grained.

Hot wallets (open positions) polled every ~15s; idle registered wallets every ~3 min.

No trading. No private keys from users. Watch-only.
"""


START_TEXT = """\
👋 Veranta liquidation / TP-SL alert bot (watch-only).

Send your Base wallet address to subscribe, e.g.
<code>0x1234...abcd</code>

I'll confirm open-position count, poll hot ~15s / idle ~3 min, and ping on:
• Liq proximity (default 45 / 25 / 10 collateral points away)
• TP / SL proximity (default within 10 PnL pts)
• Concise closed-position notices

Configure with /settings · preview with /test · /positions · /help
"""
