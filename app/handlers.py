"""Telegram command / message / callback handlers."""

from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass
from io import BytesIO

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .veranta_sdk import VerantaSDK, Position, is_valid_address, normalize_address
from .chart import chart_from_position
from .config import LIQ_PNL_PERCENT
from .db import Database
from .formatting import (
    HELP_TEXT,
    START_TEXT,
    alert_keyboard,
    fit_caption,
    format_liq_alert,
    format_positions,
    format_settings,
    format_top_position_summary,
    html_photo_kwargs,
    html_send_kwargs,
    pick_closest_to_liq,
    positions_keyboard,
    preview_alert_level,
    settings_keyboard,
    short_wallet,
    format_position_card,
)
from .prefs import (
    LIQ_PRESETS,
    cycle_liq_preset,
    format_liq_levels,
)

logger = logging.getLogger(__name__)

_HTML = html_send_kwargs()


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _client(context: ContextTypes.DEFAULT_TYPE) -> VerantaSDK:
    return context.application.bot_data["veranta"]


def _png_buf(png: bytes, name: str) -> BytesIO:
    bio = BytesIO(png)
    bio.name = name
    return bio


async def _safe_delete(message) -> None:  # noqa: ANN001
    if message is None:
        return
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass


@dataclass
class PositionsLoad:
    html: str
    positions: list[Position]
    muted: bool


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    await update.message.reply_text(START_TEXT, **_HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(HELP_TEXT, **_HTML)


async def _load_positions(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> PositionsLoad:
    sub = await _db(context).get_subscription(chat_id)
    if not sub:
        return PositionsLoad(
            "You're not subscribed yet. Send a Base wallet address "
            "(<code>0x…</code>).",
            [],
            False,
        )
    try:
        positions = await _client(context).fetch_positions(sub.wallet)
    except Exception as exc:  # noqa: BLE001
        logger.exception("positions fetch failed chat=%s", chat_id)
        from html import escape as html_escape

        return PositionsLoad(
            "Couldn't reach Veranta right now. Try again shortly.\n"
            "",
            [],
            False,
        )
    text = format_positions(positions, sub.wallet)
    if sub.muted and not positions:
        text += "\n\n<i>Alerts muted — /unmute to resume</i>"
    return PositionsLoad(text, positions, bool(sub.muted))


async def _send_position_photos(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    positions: list[Position],
    *,
    muted: bool,
) -> None:
    ordered = sorted(positions, key=lambda x: (x.pair_index, x.index))
    rendered: list[tuple[str, bytes | None]] = []
    for i, pos in enumerate(ordered):
        caption = format_position_card(pos)
        if muted and i == len(ordered) - 1:
            caption += "\n\n<i>Alerts muted — /unmute to resume</i>"
        caption = fit_caption(caption)
        png: bytes | None = None
        try:
            png = await asyncio.to_thread(chart_from_position, pos)
        except Exception:  # noqa: BLE001
            logger.exception(
                "positions chart failed pair=%s idx=%s",
                pos.symbol,
                pos.index,
            )
        rendered.append((caption, png))

    # Never album: one send_photo per position so LONG/SHORT are each their own card.
    kb = positions_keyboard()
    for i, (cap, png) in enumerate(rendered):
        markup = kb if i == 0 else None
        try:
            if png:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=_png_buf(png, f"pos_{i}.png"),
                    filename=f"pos_{i}.png",
                    caption=cap,
                    **html_photo_kwargs(reply_markup=markup),
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=cap,
                    reply_markup=markup,
                    **_HTML,
                )
        except Exception:  # noqa: BLE001
            logger.exception("sequential position send failed i=%s", i)
            await context.bot.send_message(chat_id=chat_id, text=cap, **_HTML)


async def _deliver_positions(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    load: PositionsLoad,
    *,
    status_message=None,  # noqa: ANN001
) -> None:
    if not load.positions:
        if status_message is not None:
            try:
                await status_message.edit_text(load.html, **_HTML)
                return
            except BadRequest:
                pass
        await context.bot.send_message(chat_id=chat_id, text=load.html, **_HTML)
        await _safe_delete(status_message)
        return

    try:
        await _send_position_photos(
            context, chat_id, load.positions, muted=load.muted
        )
    except Exception:  # noqa: BLE001
        logger.exception("position photo delivery failed; text fallback")
        await context.bot.send_message(
            chat_id=chat_id,
            text=load.html,
            reply_markup=positions_keyboard(),
            **_HTML,
        )
    await _safe_delete(status_message)


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    status = await update.message.reply_text("Fetching positions…")
    load = await _load_positions(context, update.effective_chat.id)
    await _deliver_positions(
        context, update.effective_chat.id, load, status_message=status
    )


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    ok = await _db(context).set_muted(update.effective_chat.id, True)
    if not ok:
        await update.message.reply_text("No active subscription. Send a wallet first.")
        return
    await update.message.reply_text("Muted. I'll keep watching but won't ping you.")


async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    ok = await _db(context).set_muted(update.effective_chat.id, False)
    if not ok:
        await update.message.reply_text("No active subscription. Send a wallet first.")
        return
    await update.message.reply_text("Unmuted. Alerts are on again.")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    ok = await _db(context).delete_subscription(update.effective_chat.id)
    if not ok:
        await update.message.reply_text("Nothing to stop — you're not subscribed.")
        return
    await update.message.reply_text("Unsubscribed. Send a wallet anytime to start again.")


async def _reply_settings(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    message=None,  # noqa: ANN001
    edit_message=None,  # noqa: ANN001
) -> None:
    db = _db(context)
    sub = await db.get_subscription(chat_id)
    prefs = await db.ensure_alert_prefs(chat_id)
    text = format_settings(prefs, muted=bool(sub.muted) if sub else False)
    kb = settings_keyboard(prefs)
    if edit_message is not None:
        try:
            await edit_message.edit_text(text, reply_markup=kb, **_HTML)
        except BadRequest as exc:
            # Ignore "message is not modified" from rapid taps.
            if "not modified" not in str(exc).lower():
                raise
        return
    if message is not None:
        await message.reply_text(text, reply_markup=kb, **_HTML)


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show / adjust alert preferences.

    Optional args:
      /settings tp 15
      /settings sl 5
      /settings liq safe|default|tight
    """
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    db = _db(context)
    sub = await db.get_subscription(chat_id)
    if not sub:
        await update.message.reply_text(
            "Subscribe first — send a Base wallet address (<code>0x…</code>), "
            "then /settings.",
            **_HTML,
        )
        return

    args = context.args or []
    if len(args) >= 2:
        key = args[0].lower().strip()
        val = args[1].lower().strip()
        prefs = await db.ensure_alert_prefs(chat_id)
        try:
            if key in ("tp", "tp_distance", "tpdist"):
                dist = float(val)
                if dist < 0 or dist > 100:
                    raise ValueError("distance out of range")
                prefs = prefs.with_updates(tp_distance_pnl=dist)
                await db.save_alert_prefs(prefs)
            elif key in ("sl", "sl_distance", "sldist"):
                dist = float(val)
                if dist < 0 or dist > 100:
                    raise ValueError("distance out of range")
                prefs = prefs.with_updates(sl_distance_pnl=dist)
                await db.save_alert_prefs(prefs)
            elif key in ("liq", "liq_levels", "levels"):
                if val not in LIQ_PRESETS:
                    await update.message.reply_text(
                        "Unknown liq preset. Use: safe · default · tight"
                    )
                    return
                prefs = prefs.with_updates(liq_levels=LIQ_PRESETS[val])
                await db.save_alert_prefs(prefs)
            else:
                await update.message.reply_text(
                    "Usage:\n"
                    "<code>/settings</code>\n"
                    "<code>/settings tp 15</code>\n"
                    "<code>/settings sl 5</code>\n"
                    "<code>/settings liq safe|default|tight</code>",
                    **_HTML,
                )
                return
        except ValueError:
            await update.message.reply_text(
                "Invalid value. Distances are PnL pts (e.g. 5, 10, 20)."
            )
            return

    await _reply_settings(context, chat_id, message=update.message)


async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a preview near-liq alert + chart without advancing alert state."""
    if not update.effective_chat or not update.message:
        return
    sub = await _db(context).get_subscription(update.effective_chat.id)
    if not sub:
        await update.message.reply_text(
            "Subscribe first — send a Base wallet address (<code>0x…</code>).",
            **_HTML,
        )
        return
    status = await update.message.reply_text("Building preview…")
    try:
        positions = await _client(context).fetch_positions(sub.wallet)
    except Exception as exc:  # noqa: BLE001
        logger.exception("test preview fetch failed")
        from html import escape as html_escape

        await status.edit_text(
            "Couldn't reach Veranta right now. Try again shortly.\n"
            "",
            **_HTML,
        )
        return
    if not positions:
        await status.edit_text(
            f"No open positions for <code>{short_wallet(sub.wallet)}</code> "
            "— nothing to preview.",
            **_HTML,
        )
        return

    prefs = await _db(context).get_alert_prefs(update.effective_chat.id)
    pos = pick_closest_to_liq(positions)
    assert pos is not None
    pnl_pct = pos.unrealized_pnl_pct
    if pnl_pct is None:
        pnl_pct = 0.0
    level = preview_alert_level(pos, prefs.liq_levels)
    caption = fit_caption(
        format_liq_alert(
            pos,
            level,
            pnl_pct,
            liq_pnl_percent=LIQ_PNL_PERCENT,
            preview=True,
        )
    )
    photo = None
    try:
        photo = await asyncio.to_thread(chart_from_position, pos, pnl_usdc=pos.unrealized_pnl_usdc)
    except Exception:  # noqa: BLE001
        logger.exception("test chart render failed")

    kb = alert_keyboard()
    try:
        if photo:
            await update.message.reply_photo(
                photo=_png_buf(photo, "preview.png"),
                filename="preview.png",
                caption=caption,
                **html_photo_kwargs(reply_markup=kb),
            )
        else:
            await update.message.reply_text(
                caption,
                reply_markup=kb,
                **_HTML,
            )
    except Exception:  # noqa: BLE001
        logger.exception("test preview send failed")
        await update.message.reply_text(
            caption,
            reply_markup=kb,
            **_HTML,
        )
    await _safe_delete(status)


async def _apply_settings_callback(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, data: str
) -> str | None:
    """Mutate prefs from callback data. Returns short toast text or None."""
    db = _db(context)
    prefs = await db.ensure_alert_prefs(chat_id)
    parts = data.split(":")
    # set:toggle:liq | set:liq:safe | set:tpdist:10 | set:sldist:5 | set:liq:cycle
    if len(parts) < 3:
        return None
    _, action, value = parts[0], parts[1], parts[2]

    if action == "toggle":
        if value == "liq":
            prefs = prefs.with_updates(liq_enabled=not prefs.liq_enabled)
            toast = f"Liq {_on(prefs.liq_enabled)}"
        elif value == "tp":
            prefs = prefs.with_updates(tp_enabled=not prefs.tp_enabled)
            toast = f"TP {_on(prefs.tp_enabled)}"
        elif value == "sl":
            prefs = prefs.with_updates(sl_enabled=not prefs.sl_enabled)
            toast = f"SL {_on(prefs.sl_enabled)}"
        else:
            return None
        await db.save_alert_prefs(prefs)
        return toast

    if action == "liq":
        if value == "cycle":
            name, levels = cycle_liq_preset(prefs.liq_levels)
            prefs = prefs.with_updates(liq_levels=levels)
            await db.save_alert_prefs(prefs)
            return f"Liq preset: {name} ({format_liq_levels(levels)})"
        if value in LIQ_PRESETS:
            levels = LIQ_PRESETS[value]
            prefs = prefs.with_updates(liq_levels=levels)
            await db.save_alert_prefs(prefs)
            return f"Liq: {value} ({format_liq_levels(levels)})"
        return None

    if action == "tpdist":
        try:
            dist = float(value)
        except ValueError:
            return None
        prefs = prefs.with_updates(tp_distance_pnl=dist)
        await db.save_alert_prefs(prefs)
        return f"TP distance: {dist:g} PnL pts"

    if action == "sldist":
        try:
            dist = float(value)
        except ValueError:
            return None
        prefs = prefs.with_updates(sl_distance_pnl=dist)
        await db.save_alert_prefs(prefs)
        return f"SL distance: {dist:g} PnL pts"

    return None


def _on(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_chat:
        return
    data = (query.data or "").strip()
    chat_id = update.effective_chat.id

    if data == "positions":
        await query.answer("Fetching positions…")
        load = await _load_positions(context, chat_id)
        await _deliver_positions(context, chat_id, load)
        return

    if data == "mute":
        ok = await _db(context).set_muted(chat_id, True)
        if not ok:
            await query.answer("No subscription", show_alert=True)
            return
        await query.answer("Muted")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Muted. I'll keep watching but won't ping you. /unmute to resume.",
        )
        return

    if data == "settings":
        sub = await _db(context).get_subscription(chat_id)
        if not sub:
            await query.answer("Subscribe first", show_alert=True)
            return
        await query.answer()
        # New message — callback may be on a photo caption that can't edit as text.
        prefs = await _db(context).ensure_alert_prefs(chat_id)
        text = format_settings(prefs, muted=bool(sub.muted))
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=settings_keyboard(prefs),
            **_HTML,
        )
        return

    if data.startswith("set:"):
        sub = await _db(context).get_subscription(chat_id)
        if not sub:
            await query.answer("Subscribe first", show_alert=True)
            return
        toast = await _apply_settings_callback(context, chat_id, data)
        await query.answer(toast or "Updated")
        if query.message:
            await _reply_settings(
                context, chat_id, edit_message=query.message
            )
        return

    await query.answer()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    candidate = text.strip("`").strip()
    if not is_valid_address(candidate):
        await update.message.reply_text(
            "Send a valid Base wallet address: <code>0x</code> followed by "
            "40 hex characters.\nOr use /help.",
            **_HTML,
        )
        return
    wallet = normalize_address(candidate)
    sub = await _db(context).upsert_subscription(update.effective_chat.id, wallet)
    short = short_wallet(sub.wallet)
    prefs = await _db(context).get_alert_prefs(sub.chat_id)
    levels_s = format_liq_levels(prefs.liq_levels)

    try:
        positions = await _client(context).fetch_positions(sub.wallet)
    except Exception as exc:  # noqa: BLE001
        logger.exception("initial positions fetch failed")
        from html import escape as html_escape

        await update.message.reply_text(
            f"Watching <code>{short}</code> — subscribed, but Veranta fetch "
            f"failed just now.\n({html_escape(str(exc), quote=False)})\n"
            f"/positions · /test · /settings · /mute · /stop",
            **_HTML,
        )
        return

    count = len(positions)
    if positions:
        await _db(context).set_hot(sub.chat_id, True)
        top = pick_closest_to_liq(positions) or positions[0]
        summary = format_top_position_summary(top)
        await update.message.reply_text(
            f"Watching <code>{short}</code> · "
            f"<b>{count} open position{'s' if count != 1 else ''}</b>\n"
            f"{summary}\n\n"
            f"Liq alerts at net PnL {levels_s} (liq ≈ -85%). "
            f"TP/SL proximity ≤ {prefs.tp_distance_pnl:g}/{prefs.sl_distance_pnl:g} "
            f"PnL pts. Hot poll ~15s.\n"
            f"/positions · /test · /settings · /mute · /stop",
            **_HTML,
        )
    else:
        await _db(context).set_hot(sub.chat_id, False)
        await update.message.reply_text(
            f"Watching <code>{short}</code> · <b>0 open positions</b>\n"
            f"Idle cadence (~3 min) until you open a trade on Veranta.\n"
            f"/positions · /test · /settings · /mute · /stop",
            **_HTML,
        )
