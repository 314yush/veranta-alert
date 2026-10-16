"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _parse_pnl_levels(raw: str) -> tuple[float, ...]:
    """Parse comma-separated PnL% alert levels (negative toward liquidation)."""
    levels: list[float] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        levels.append(float(part))
    if not levels:
        levels = [-40.0, -60.0, -75.0]
    # Most severe (most negative) last so evaluation order is warn → high → critical
    return tuple(sorted(levels, reverse=True))


# PnL% of collateral alert levels (toward Veranta liq ≈ -85%).
# Prefer env PNL_ALERT_LEVELS; LIQ_THRESHOLDS kept as deprecated alias name in docs only.
PNL_ALERT_LEVELS = _parse_pnl_levels(os.getenv("PNL_ALERT_LEVELS", "-40,-60,-75"))
LIQ_PNL_PERCENT = float(os.getenv("LIQ_PNL_PERCENT", "-85"))
# Back-compat alias used by older imports (same tuple as PNL_ALERT_LEVELS).
LIQ_THRESHOLDS = PNL_ALERT_LEVELS


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    veranta_private_key: str
    veranta_trader_address: str
    veranta_network: str
    hot_poll_seconds: float
    idle_poll_seconds: float
    alert_cooldown_seconds: float
    hysteresis_percent: float
    pnl_alert_levels: tuple[float, ...]
    liq_pnl_percent: float
    sqlite_path: Path
    log_level: str
    mark_max_age_seconds: float
    hermes_rest_url: str


def _require(name: str, hint: str) -> str:
    value = (os.getenv(name) or os.getenv(name.replace("VERANTA_", "AVANTIS_")) or "").strip()
    if not value:
        raise SystemExit(f"{name} is missing.\n{hint}")
    return value


def load_settings() -> Settings:
    token = _require(
        "TELEGRAM_BOT_TOKEN",
        "1) Message @BotFather on Telegram → /newbot\n"
        "2) Copy the token into .env (see .env.example)\n"
        "3) Re-run the bot",
    )
    private_key = _require(
        "VERANTA_PRIVATE_KEY",
        "Generate a service API/delegate key at https://delegate.veranta.xyz/\n"
        "and set VERANTA_PRIVATE_KEY in .env (Model A — service-owned only).",
    )
    trader = _require(
        "VERANTA_TRADER_ADDRESS",
        "Set VERANTA_TRADER_ADDRESS to the operator wallet that authorized\n"
        "the API key (https://delegate.veranta.xyz/).",
    )
    db = Path(os.getenv("SQLITE_PATH", "data/bot.db"))
    if not db.is_absolute():
        db = ROOT / db
    return Settings(
        telegram_bot_token=token,
        veranta_private_key=private_key,
        veranta_trader_address=trader,
        veranta_network=(os.getenv("VERANTA_NETWORK") or os.getenv("AVANTIS_NETWORK") or "mainnet").strip().lower(),
        hot_poll_seconds=float(os.getenv("HOT_POLL_SECONDS", "15")),
        idle_poll_seconds=float(os.getenv("IDLE_POLL_SECONDS", "180")),
        alert_cooldown_seconds=float(os.getenv("ALERT_COOLDOWN_SECONDS", "300")),
        hysteresis_percent=float(os.getenv("HYSTERESIS_PERCENT", "0.5")),
        pnl_alert_levels=_parse_pnl_levels(
            os.getenv("PNL_ALERT_LEVELS", "-40,-60,-75")
        ),
        liq_pnl_percent=float(os.getenv("LIQ_PNL_PERCENT", "-85")),
        sqlite_path=db,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        mark_max_age_seconds=float(os.getenv("MARK_MAX_AGE_SECONDS", "120")),
        hermes_rest_url=(
            os.getenv("HERMES_REST_URL")
            or "https://hermes.pyth.network/v2/updates/price/latest"
        ).strip(),
    )
