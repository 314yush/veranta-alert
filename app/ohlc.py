"""Public OHLC candles for Veranta crypto pairs.

External reference-candle fallback. For mapped crypto symbols we pull
public exchange klines (Binance data-api → Binance.com → OKX → Coinbase);
unmapped pairs (XAU, FX, etc.) return None so the chart layer can fall
back to a Pillow level-card.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Veranta display symbol → exchange symbols.
# Keys normalized: upper, strip spaces; also accept BASE without /USD.
SYMBOL_MAP: dict[str, dict[str, str]] = {
    # binance = USDT pair, okx = BTC-USDT, coinbase = BTC-USD
    "BTC": {"binance": "BTCUSDT", "okx": "BTC-USDT", "coinbase": "BTC-USD"},
    "ETH": {"binance": "ETHUSDT", "okx": "ETH-USDT", "coinbase": "ETH-USD"},
    "SOL": {"binance": "SOLUSDT", "okx": "SOL-USDT", "coinbase": "SOL-USD"},
    "BNB": {"binance": "BNBUSDT", "okx": "BNB-USDT", "coinbase": "BNB-USD"},
    "XRP": {"binance": "XRPUSDT", "okx": "XRP-USDT", "coinbase": "XRP-USD"},
    "DOGE": {"binance": "DOGEUSDT", "okx": "DOGE-USDT", "coinbase": "DOGE-USD"},
    "AVAX": {"binance": "AVAXUSDT", "okx": "AVAX-USDT", "coinbase": "AVAX-USD"},
    "LINK": {"binance": "LINKUSDT", "okx": "LINK-USDT", "coinbase": "LINK-USD"},
    "ARB": {"binance": "ARBUSDT", "okx": "ARB-USDT", "coinbase": "ARB-USD"},
    "OP": {"binance": "OPUSDT", "okx": "OP-USDT", "coinbase": "OP-USD"},
    "MATIC": {"binance": "MATICUSDT", "okx": "MATIC-USDT", "coinbase": "MATIC-USD"},
    "POL": {"binance": "POLUSDT", "okx": "POL-USDT", "coinbase": "POL-USD"},
    "ADA": {"binance": "ADAUSDT", "okx": "ADA-USDT", "coinbase": "ADA-USD"},
    "SUI": {"binance": "SUIUSDT", "okx": "SUI-USDT", "coinbase": "SUI-USD"},
    "PEPE": {"binance": "PEPEUSDT", "okx": "PEPE-USDT", "coinbase": "PEPE-USD"},
    "WIF": {"binance": "WIFUSDT", "okx": "WIF-USDT", "coinbase": "WIF-USD"},
    "NEAR": {"binance": "NEARUSDT", "okx": "NEAR-USDT", "coinbase": "NEAR-USD"},
    "APT": {"binance": "APTUSDT", "okx": "APT-USDT", "coinbase": "APT-USD"},
    "SEI": {"binance": "SEIUSDT", "okx": "SEI-USDT", "coinbase": "SEI-USD"},
    "TIA": {"binance": "TIAUSDT", "okx": "TIA-USDT", "coinbase": "TIA-USD"},
    "INJ": {"binance": "INJUSDT", "okx": "INJ-USDT", "coinbase": "INJ-USD"},
    "LTC": {"binance": "LTCUSDT", "okx": "LTC-USDT", "coinbase": "LTC-USD"},
    "BCH": {"binance": "BCHUSDT", "okx": "BCH-USDT", "coinbase": "BCH-USD"},
    "UNI": {"binance": "UNIUSDT", "okx": "UNI-USDT", "coinbase": "UNI-USD"},
    "AAVE": {"binance": "AAVEUSDT", "okx": "AAVE-USDT", "coinbase": "AAVE-USD"},
    "CRV": {"binance": "CRVUSDT", "okx": "CRV-USDT", "coinbase": "CRV-USD"},
    "MKR": {"binance": "MKRUSDT", "okx": "MKR-USDT", "coinbase": "MKR-USD"},
    "WLD": {"binance": "WLDUSDT", "okx": "WLD-USDT", "coinbase": "WLD-USD"},
    "FIL": {"binance": "FILUSDT", "okx": "FIL-USDT", "coinbase": "FIL-USD"},
    "ATOM": {"binance": "ATOMUSDT", "okx": "ATOM-USDT", "coinbase": "ATOM-USD"},
    "DOT": {"binance": "DOTUSDT", "okx": "DOT-USDT", "coinbase": "DOT-USD"},
    "TRX": {"binance": "TRXUSDT", "okx": "TRX-USDT", "coinbase": "TRX-USD"},
    "TON": {"binance": "TONUSDT", "okx": "TON-USDT", "coinbase": "TON-USD"},
    "ENA": {"binance": "ENAUSDT", "okx": "ENA-USDT", "coinbase": "ENA-USD"},
    "EIGEN": {"binance": "EIGENUSDT", "okx": "EIGEN-USDT", "coinbase": "EIGEN-USD"},
    "PENDLE": {"binance": "PENDLEUSDT", "okx": "PENDLE-USDT", "coinbase": "PENDLE-USD"},
    "JUP": {"binance": "JUPUSDT", "okx": "JUP-USDT", "coinbase": "JUP-USD"},
    "TAO": {"binance": "TAOUSDT", "okx": "TAO-USDT", "coinbase": "TAO-USD"},
    "RENDER": {"binance": "RENDERUSDT", "okx": "RENDER-USDT", "coinbase": "RENDER-USD"},
    "FET": {"binance": "FETUSDT", "okx": "FET-USDT", "coinbase": "FET-USD"},
}

# Back-compat alias used by smoke / docs.
SYMBOL_TO_BINANCE: dict[str, str] = {}
for _base, _m in SYMBOL_MAP.items():
    SYMBOL_TO_BINANCE[_base] = _m["binance"]
    SYMBOL_TO_BINANCE[f"{_base}/USD"] = _m["binance"]

BINANCE_URLS = (
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
    "https://api.binance.us/api/v3/klines",
)
OKX_URL = "https://www.okx.com/api/v5/market/candles"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"

DEFAULT_INTERVAL = "15m"
DEFAULT_LIMIT = 96  # 96 × 15m ≈ 24h
REQUEST_TIMEOUT_S = 2.5
CACHE_TTL_S = 45.0
_UA = {"User-Agent": "veranta-alert-bot/1.0"}

# (base, interval, limit) → (expires_at, candles)
_cache: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}


def normalize_pair(symbol: str | None) -> str:
    if not symbol:
        return ""
    s = str(symbol).strip().upper().replace(" ", "")
    if s.endswith("/USDT"):
        s = s[:-5] + "/USD"
    return s


def is_exotic_pair(symbol: str | None) -> bool:
    """True for Veranta products that must never use spot OHLC (e.g. XRP_UPSIDE)."""
    s = normalize_pair(symbol)
    if not s:
        return False
    head = s.split("/", 1)[0]
    return (
        "_UPSIDE" in head
        or "_DOWNSIDE" in head
        or head.endswith("UPSIDE")
        or head.endswith("DOWNSIDE")
    )


def _base_for(pair: str | None) -> str | None:
    if is_exotic_pair(pair):
        return None
    s = normalize_pair(pair)
    if not s:
        return None
    base = s.split("/")[0] if "/" in s else s
    return base if base in SYMBOL_MAP else None


def binance_symbol_for(pair: str | None) -> str | None:
    """Return Binance symbol for an Veranta pair label, or None if unmapped."""
    base = _base_for(pair)
    if not base:
        return None
    return SYMBOL_MAP[base]["binance"]


def _parse_binance(raw: Any) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for row in raw:
        candles.append(
            {
                "time": int(row[0]) // 1000,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }
        )
    return candles


def _fetch_binance(
    client: httpx.Client, symbol: str, interval: str, limit: int
) -> list[dict[str, Any]] | None:
    params = {"symbol": symbol, "interval": interval, "limit": int(limit)}
    last_err: Exception | None = None
    for url in BINANCE_URLS:
        try:
            resp = client.get(url, params=params, headers=_UA)
            if resp.status_code == 451:
                last_err = RuntimeError(f"451 from {url}")
                continue
            resp.raise_for_status()
            candles = _parse_binance(resp.json())
            if candles:
                return candles
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    if last_err:
        logger.debug("ohlc binance exhausted: %s", last_err)
    return None


def _fetch_okx(
    client: httpx.Client, inst: str, interval: str, limit: int
) -> list[dict[str, Any]] | None:
    # OKX bars: 1m/3m/5m/15m/1H/...
    bar = interval
    if interval.endswith("h") or interval.endswith("H"):
        bar = interval.upper() if interval[:-1].isdigit() else interval
    try:
        resp = client.get(
            OKX_URL,
            params={"instId": inst, "bar": bar, "limit": str(int(limit))},
            headers=_UA,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("ohlc okx failed: %s", exc)
        return None
    # OKX returns newest-first: [ts, o, h, l, c, ...]
    candles: list[dict[str, Any]] = []
    for row in data:
        candles.append(
            {
                "time": int(row[0]) // 1000,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }
        )
    candles.sort(key=lambda c: c["time"])
    return candles or None


def _fetch_coinbase(
    client: httpx.Client, product: str, interval: str, limit: int
) -> list[dict[str, Any]] | None:
    gran = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1H": 3600}.get(interval)
    if not gran:
        return None
    try:
        resp = client.get(
            COINBASE_URL.format(product=product),
            params={"granularity": gran},
            headers=_UA,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("ohlc coinbase failed: %s", exc)
        return None
    # Coinbase: [time, low, high, open, close, volume], newest first
    candles: list[dict[str, Any]] = []
    for row in raw:
        candles.append(
            {
                "time": int(row[0]),
                "low": float(row[1]),
                "high": float(row[2]),
                "open": float(row[3]),
                "close": float(row[4]),
            }
        )
    candles.sort(key=lambda c: c["time"])
    if limit and len(candles) > limit:
        candles = candles[-int(limit) :]
    return candles or None


def fetch_candles(
    pair: str | None,
    *,
    interval: str = DEFAULT_INTERVAL,
    limit: int = DEFAULT_LIMIT,
    timeout: float = REQUEST_TIMEOUT_S,
) -> list[dict[str, Any]] | None:
    """Fetch last N OHLC candles as ``{time, open, high, low, close}``.

    ``time`` is unix seconds. Returns None if the pair is unmapped or all
    public sources fail / time out.
    """
    if is_exotic_pair(pair):
        logger.info("ohlc: skip exotic %r (never map to spot candles)", pair)
        return None
    base = _base_for(pair)
    if not base:
        logger.debug("ohlc: no map for %r", pair)
        return None

    key = (base, interval, int(limit))
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]

    mapping = SYMBOL_MAP[base]
    t0 = time.perf_counter()
    candles: list[dict[str, Any]] | None = None
    source = ""

    try:
        with httpx.Client(timeout=timeout) as client:
            candles = _fetch_binance(client, mapping["binance"], interval, limit)
            if candles:
                source = "binance"
            else:
                candles = _fetch_okx(client, mapping["okx"], interval, limit)
                if candles:
                    source = "okx"
                else:
                    cb = mapping.get("coinbase")
                    if cb:
                        candles = _fetch_coinbase(client, cb, interval, limit)
                        if candles:
                            source = "coinbase"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ohlc: fetch failed for %s: %s", base, exc)
        return None

    if not candles:
        logger.warning("ohlc: all sources failed for %s", base)
        return None

    _cache[key] = (now + CACHE_TTL_S, candles)
    ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "ohlc: %s via %s %s x%d in %.0fms (cache_ttl=%.0fs)",
        base,
        source,
        interval,
        len(candles),
        ms,
        CACHE_TTL_S,
    )
    return candles


def clear_cache() -> None:
    _cache.clear()
