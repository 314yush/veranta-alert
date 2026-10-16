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


def positions_keyboard() -> InlineKeyboardMarkup:
    """Inline actions on /positions photo replies (no Mute)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Settings", callback_data="settings"),
                InlineKeyboardButton("Open Veranta", url=VERANTA_URL),
            ],
        ]
    )


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
    levels_s = format_liq_levels(prefs.liq_levels)
    preset = _preset_name_for_levels(prefs.liq_levels)
    preset_s = f" ({_h(preset)})" if preset else ""
    mute_note = (
        "\n\n⏸ Global /mute is active — all alerts paused." if muted else ""
    )
    return (
        "⚙️ <b>Alert settings</b>\n"
        f"• Liquidation: <b>{_on_off(prefs.liq_enabled)}</b> · "
        f"levels <code>{_h(levels_s)}</code>{preset_s}\n"
        f"• Take-profit proximity: <b>{_on_off(prefs.tp_enabled)}</b> · "
        f"within <code>{prefs.tp_distance_pnl:g}</code> PnL pts\n"
        f"• Stop-loss proximity: <b>{_on_off(prefs.sl_enabled)}</b> · "
        f"within <code>{prefs.sl_distance_pnl:g}</code> PnL pts\n\n"
        "Distances are <b>PnL% of collateral</b> (leverage-normalized), "
        f"not raw price%. Liq ≈ <code>{LIQ_PNL_PERCENT:g}%</code> PnL.\n\n"
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


def format_positions(positions: list[Position], wallet: str) -> str:
    """HTML text for empty-state / fallback. No marketing URL."""
    short = short_wallet(wallet)
    if not positions:
        return f"No open Veranta positions for <code>{_h(short)}</code>."

    header = f"<b>{_h(short)}</b> · {len(positions)} open"
    cards: list[str] = []
    shown = 0
    for p in sorted(positions, key=lambda x: (x.pair_index, x.index)):
        if shown >= _MAX_POSITIONS_SHOWN:
            break
        cards.append(format_position_card(p))
        shown += 1

    body = f"{header}\n\n" + "\n\n".join(cards)
    omitted = len(positions) - shown
    if omitted > 0:
        body += f"\n\n…and {omitted} more"

    if len(body) > _TELEGRAM_MSG_LIMIT:
        while cards and len(body) > _TELEGRAM_MSG_LIMIT:
            cards.pop()
            omitted = len(positions) - len(cards)
            body = (
                f"{header}\n\n"
                + "\n\n".join(cards)
                + f"\n\n…and {omitted} more"
            )
    return body


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
    buffer = pnl_pct - liq_pnl_percent
    severity = {
        -40.0: "warn",
        -60.0: "high",
        -75.0: "critical",
        -30.0: "warn",
        -50.0: "high",
        -70.0: "critical",
        -82.0: "critical",
    }.get(float(level), "near")
    emoji = {"warn": "⚠️", "high": "🟠", "critical": "🔴"}.get(severity, "⚠️")
    pnl_usdc_s = _signed_money(pos.unrealized_pnl_usdc)
    prefix = (
        "🧪 Preview /test — not a real alert (state unchanged)\n\n" if preview else ""
    )
    name = display_pair(pos.symbol)
    return (
        f"{prefix}"
        f"{emoji} <b>{_h(name)} {_h(pos.side)}</b> near liquidation\n"
        f"PnL ~{_h(pnl_usdc_s)} (~{_h(_pnl_pct_label(pnl_pct))}) · "
        f"Liq at {_h(_pnl_pct_label(liq_pnl_percent))} · "
        f"Buffer ~{buffer:.0f} pts\n"
        f"Alert level: {_h(_pnl_pct_label(level))}\n"
        f"Mark ~{_h(_money(pos.mark_price))} | Open ~{_h(_money(pos.open_price))} | "
        f"Liq ~{_h(_money(pos.liquidation_price))}\n"
        f"Size: {_h(_money(pos.collateral_usdc))} collateral @ {pos.leverage:.0f}x"
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
        f"{emoji} <b>{_h(name)} {_h(pos.side)}</b> — {label}\n"
        f"PnL now ~{_h(_pnl_pct_label(current_pnl_pct))} · "
        f"{target_name} level ~{_h(_pnl_pct_label(target_pnl_pct))} · "
        f"{remaining_pts:.1f} PnL pts away "
        f"(alert ≤ {distance_cfg:g})\n"
        f"Mark ~{_h(_money(pos.mark_price))} | "
        f"{target_name} ~{_h(_money(target_price))} | "
        f"Open ~{_h(_money(pos.open_price))}\n"
        f"Size: {_h(_money(pos.collateral_usdc))} @ {pos.leverage:.0f}x"
    )


def format_exit_alert(kind: str, pos: Position, mark: float | None) -> str:
    emoji = {
        "tp": "✅",
        "sl": "🛑",
        "liq": "💀",
        "closed": "ℹ️",
    }.get(kind, "ℹ️")
    label = {
        "tp": "Take-profit hit (best-effort)",
        "sl": "Stop-loss hit (best-effort)",
        "liq": "Liquidation (best-effort)",
        "closed": "Position no longer open — execution reason unconfirmed",
    }.get(kind, "Position update")
    name = display_pair(pos.symbol)
    return (
        f"{emoji} <b>{_h(name)} {_h(pos.side)}</b> — {label}\n"
        f"Last mark ~{_h(_money(mark))} | Entry {_h(_money(pos.open_price))} | "
        f"Liq {_h(_money(pos.liquidation_price))}\n"
        f"TP {_h(_money(pos.tp) if pos.tp else '—')} | "
        f"SL {_h(_money(pos.sl) if pos.sl else '—')}\n"
        f"Collateral was {_h(_money(pos.collateral_usdc))} @ {pos.leverage:.0f}x"
    )


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

<b>Alerts</b> (PnL% of collateral — leverage-normalized, not raw price%):
• Liquidation proximity at your levels (default -40 / -60 / -75 toward liq ≈ -85%)
• TP proximity when within N PnL pts of take-profit (default 10)
• SL proximity when within N PnL pts of stop-loss (default 10)
• Best-effort TP / SL / liq when a position disappears (respects toggles)
• Near-liq (+ optional TP/SL) charts: open / mark / liq / TP / SL
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
• Liq proximity (default -40 / -60 / -75 toward -85% PnL)
• TP / SL proximity (default within 10 PnL pts)
• Best-effort TP / SL / liq on close

Configure with /settings · preview with /test · /positions · /help
"""
