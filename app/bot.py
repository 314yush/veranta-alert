"""Application entrypoint for the Veranta alert Telegram bot."""

from __future__ import annotations

import logging
import re

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .alerts import AlertEngine
from .veranta_sdk import VerantaSDK
from .config import load_settings
from .db import Database
from .handlers import (
    help_cmd,
    mute_cmd,
    on_callback,
    on_text,
    positions_cmd,
    settings_cmd,
    start,
    stop_cmd,
    test_cmd,
    unmute_cmd,
)
from .monitor import Monitor


class _RedactTelegramToken(logging.Filter):
    """Keep BotFather tokens out of httpx / PTB logs."""

    _TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if self._TOKEN_RE.search(msg):
            record.msg = self._TOKEN_RE.sub("<TELEGRAM_BOT_TOKEN>", msg)
            record.args = ()
        return True


def _configure_logging(level: str, token: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    redact = _RedactTelegramToken()
    for name in ("", "httpx", "httpcore", "telegram", "telegram.ext"):
        logging.getLogger(name).addFilter(redact)
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)
    # Also scrub exact token if present in any form
    if token:

        class _Exact(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                try:
                    msg = record.getMessage()
                except Exception:  # noqa: BLE001
                    return True
                if token in msg:
                    record.msg = msg.replace(token, "<TELEGRAM_BOT_TOKEN>")
                    record.args = ()
                return True

        for name in ("", "httpx", "httpcore", "telegram", "telegram.ext"):
            logging.getLogger(name).addFilter(_Exact())
        for handler in logging.getLogger().handlers:
            handler.addFilter(_Exact())


BOT_COMMANDS = [
    BotCommand("start", "Intro & how to subscribe"),
    BotCommand("help", "Commands and alert levels"),
    BotCommand("positions", "Show open Veranta positions"),
    BotCommand("test", "Preview near-liq alert + chart"),
    BotCommand("settings", "Alert toggles & distances"),
    BotCommand("mute", "Pause alerts"),
    BotCommand("unmute", "Resume alerts"),
    BotCommand("stop", "Unsubscribe wallet"),
]


def main() -> None:
    settings = load_settings()
    _configure_logging(settings.log_level, settings.telegram_bot_token)
    logger = logging.getLogger("veranta-alert-bot")

    db = Database(settings.sqlite_path)
    client = VerantaSDK(
        private_key=settings.veranta_private_key,
        trader_address=settings.veranta_trader_address,
        network=settings.veranta_network,
        mark_max_age_seconds=settings.mark_max_age_seconds,
        hermes_rest_url=settings.hermes_rest_url,
    )
    alerter = AlertEngine(
        db,
        cooldown_seconds=settings.alert_cooldown_seconds,
        hysteresis_percent=settings.hysteresis_percent,
        pnl_alert_levels=settings.pnl_alert_levels,
        liq_pnl_percent=settings.liq_pnl_percent,
    )

    async def post_init(app: Application) -> None:
        await db.connect()
        app.bot_data["db"] = db
        app.bot_data["veranta"] = client
        app.bot_data["alerter"] = alerter
        monitor = Monitor(app, db, client, alerter)
        app.bot_data["monitor"] = monitor
        from .limit_fills import LimitFillWatcher
        fills = LimitFillWatcher(db, client)
        monitor.fill_watcher = fills
        app.bot_data["limit_fills"] = fills
        # Bound baseline/catch-up work so RPC delays cannot block bot startup.
        import asyncio
        try:
            await asyncio.wait_for(fills.tick(None), timeout=5)
        except TimeoutError:
            logger.warning("Limit fill startup scan deferred to scheduled retry")
        app.job_queue.run_repeating(fills.tick, interval=15, first=5, name="limit-fill-monitor")
        from .health import start_health_server
        app.bot_data["health_server"] = await start_health_server(monitor)
        app.job_queue.run_repeating(monitor.deliver_pending, interval=2, first=2, name="alert-delivery")
        if app.job_queue is None:
            raise RuntimeError(
                "JobQueue missing. Install dependencies with: "
                "pip install -r requirements.txt"
            )
        try:
            await app.bot.set_my_commands(BOT_COMMANDS)
            logger.info(
                "Telegram command menu set (%d commands)", len(BOT_COMMANDS)
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to set Telegram bot commands")
        app.job_queue.run_repeating(
            monitor.tick_hot,
            interval=settings.hot_poll_seconds,
            first=5,
            name="veranta-hot-monitor",
        )
        app.job_queue.run_repeating(
            monitor.tick_idle,
            interval=settings.idle_poll_seconds,
            first=10,
            name="veranta-idle-monitor",
        )

        async def _prune_lazer_ticks(context) -> None:  # noqa: ANN001
            from .ticks import prune

            n = prune()
            if n:
                logger.info("pruned %d stale lazer ticks", n)

        try:
            from .ticks import prune as _prune_now

            _prune_now()
        except Exception:  # noqa: BLE001
            logger.debug("initial lazer tick prune skipped", exc_info=True)
        app.job_queue.run_repeating(
            _prune_lazer_ticks,
            interval=900,
            first=60,
            name="veranta-lazer-tick-prune",
        )
        logger.info(
            "Bot ready. Hot every %ss, idle every %ss. network=%s "
            "PnL levels=%s liq≈%s%% DB=%s",
            settings.hot_poll_seconds,
            settings.idle_poll_seconds,
            settings.veranta_network,
            settings.pnl_alert_levels,
            settings.liq_pnl_percent,
            settings.sqlite_path,
        )

    async def post_shutdown(app: Application) -> None:
        health = app.bot_data.get("health_server")
        if health:
            health.close()
            await health.wait_closed()
        fills = app.bot_data.get("limit_fills")
        if fills:
            await fills.close()
        await client.aclose()
        await db.close()
        try:
            from .ticks import close as close_ticks

            close_ticks()
        except Exception:  # noqa: BLE001
            pass

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("help", help_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("positions", positions_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("test", test_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("settings", settings_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("mute", mute_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("unmute", unmute_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("stop", stop_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_text)
    )

    application.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
