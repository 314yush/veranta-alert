"""Thin wrapper around veranta-trader-sdk AsyncVeranta (read-only)."""

from __future__ import annotations

import logging
import math
import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from veranta_sdk import AsyncVeranta
from veranta_sdk.compute.pnl import gross_pnl, position_net_pnl
from veranta_sdk.markets.models import PairInfo

logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# Cache pair catalog (symbols + PairInfo for net PnL fees) this long.
_PAIR_CACHE_TTL_S = 300.0

# Reject feed-v3 / oracle marks older than this (seconds).
_DEFAULT_MARK_MAX_AGE_S = 120.0
_DEFAULT_HERMES_REST = "https://hermes.pyth.network/v2/updates/price/latest"


def is_valid_address(value: str) -> bool:
    return bool(ADDRESS_RE.match((value or "").strip()))


def normalize_address(value: str) -> str:
    return value.strip().lower()


def _f(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _norm_pyth_id(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 64:
        return None
    return s


@dataclass
class Position:
    """Internal position model mapped from SDK Position human-unit fields."""

    trader: str
    pair_index: int
    index: int
    is_long: bool
    collateral_usdc: float
    leverage: float
    open_price: float
    tp: float
    sl: float
    liquidation_price: float
    mark_price: float | None = None
    symbol: str = ""
    opened_at: int | None = None
    # Net PnL (UI parity: fees/funding/rollover). Populated at fetch time.
    unrealized_pnl_usdc: float | None = None
    unrealized_pnl_pct: float | None = None
    # Gross PnL kept for debug / smoke comparison.
    gross_pnl_usdc: float | None = None
    chart_symbol: str | None = None

    @property
    def position_key(self) -> str:
        return f"{self.trader.lower()}:{self.pair_index}:{self.index}:{self.opened_at or self.open_price}"

    @property
    def chart_key(self) -> str:
        return hashlib.sha256(self.position_key.encode()).hexdigest()[:24]

    @property
    def side(self) -> str:
        return "LONG" if self.is_long else "SHORT"

    @property
    def notional_usdc(self) -> float:
        return self.collateral_usdc * self.leverage

    def buffer_to_liq_pts(
        self, mark: float | None = None, *, liq_pnl_percent: float = -85.0
    ) -> float | None:
        """Percentage-points of collateral PnL remaining before liq threshold."""
        m = self.mark_price if mark is None else mark
        if m is None or any(not math.isfinite(v) or v <= 0 for v in (m, self.liquidation_price, self.open_price, self.leverage)):
            return None
        direction = 1 if self.is_long else -1
        return (m - self.liquidation_price) * direction / self.open_price * self.leverage * 100

    def distance_to_liq_pct(self, mark: float | None = None) -> float | None:
        """Legacy mark-vs-liq price distance % (kept for debugging; alerts use PnL%).

        Long: (mark - liq) / mark * 100
        Short: (liq - mark) / mark * 100
        """
        m = self.mark_price if mark is None else mark
        liq = self.liquidation_price
        if m is None or any(not math.isfinite(v) or v <= 0 for v in (m, liq)):
            return None
        if self.is_long:
            return (m - liq) / m * 100.0
        return (liq - m) / m * 100.0


def _compute_gross(
    *,
    mark: float,
    open_price: float,
    collateral: float,
    leverage: float,
    is_long: bool,
) -> float | None:
    if any(not math.isfinite(v) or v <= 0 for v in (mark, open_price, collateral, leverage)):
        return None
    return gross_pnl(
        current_price=mark,
        open_price=open_price,
        collateral=collateral,
        leverage=leverage,
        is_long=is_long,
    )


def _pnl_for_sdk_position(
    sdk_pos: Any,
    pair_info: PairInfo | None,
    mark: float | None,
) -> tuple[float | None, float | None]:
    """Return (net_usdc, gross_usdc). Net is unavailable if fee data or computation fails; never label gross as net."""
    if mark is None or mark <= 0:
        return None, None
    collateral = _f(sdk_pos.collateral)
    leverage = _f(sdk_pos.leverage)
    open_price = _f(sdk_pos.open_price)
    is_long = bool(sdk_pos.buy)
    g = _compute_gross(
        mark=mark,
        open_price=open_price,
        collateral=collateral,
        leverage=leverage,
        is_long=is_long,
    )
    if g is None:
        return None, None
    if pair_info is None:
        return None, g
    try:
        breakdown = position_net_pnl(sdk_pos, pair_info, mark)
        net = float(breakdown.net)
        return (net if math.isfinite(net) else None), g
    except Exception:  # noqa: BLE001
        logger.warning(
            "position_net_pnl failed pair=%s idx=%s; net PnL unavailable",
            getattr(sdk_pos, "pair_index", "?"),
            getattr(sdk_pos, "index", "?"),
            exc_info=True,
        )
        return None, g


class VerantaSDK:
    """Long-lived AsyncVeranta client used only for reads (positions / prices)."""

    def __init__(
        self,
        *,
        private_key: str | None = None,
        trader_address: str | None = None,
        network: str = "mainnet",
        mark_max_age_seconds: float | None = None,
        hermes_rest_url: str | None = None,
    ) -> None:
        self._client = AsyncVeranta(
            private_key=private_key,
            trader_address=trader_address,
            network=network,
        )
        self._pair_symbols: dict[int, str] = {}
        self._pair_info: dict[int, PairInfo] = {}
        self._pairs_loaded_at: float = 0.0
        if mark_max_age_seconds is None:
            mark_max_age_seconds = float(
                os.getenv("MARK_MAX_AGE_SECONDS", str(_DEFAULT_MARK_MAX_AGE_S))
            )
        self._mark_max_age_s = float(mark_max_age_seconds)
        # Kept for optional diagnostics / smoke; not used in mark primary path.
        self._hermes_rest_url = (
            hermes_rest_url
            or os.getenv("HERMES_REST_URL")
            or _DEFAULT_HERMES_REST
        ).rstrip("?")
        # Lazer mark cache: pair_index -> (price, publish_age_s_at_rx, mono_rx)
        self._lazer_marks: dict[int, tuple[float, float, float]] = {}
        self._lazer_task: Any = None
        self._lazer_stream: Any = None
        self._lazer_subscribed_pairs: frozenset[int] = frozenset()
        self._last_mark_sources: dict[int, str] = {}

    @property
    def client(self) -> AsyncVeranta:
        return self._client

    @property
    def mark_max_age_seconds(self) -> float:
        return self._mark_max_age_s

    @property
    def last_mark_sources(self) -> dict[int, str]:
        """pair_index -> human source string from the last mark resolve."""
        return dict(self._last_mark_sources)

    async def fetch_active_twaps(self, trader: str):
        """All currently filling TWAPs; SDK list pagination is zero-based."""
        from .twaps import parse_active_twaps
        trader = normalize_address(trader)
        if not is_valid_address(trader):
            raise ValueError("Invalid trader address")
        await self.ensure_pairs()
        rows, seen = [], set()
        for page in range(20):
            batch = await self.client.account.twaps(
                trader=trader, include_canceled=False, page=page, page_size=50)
            if not isinstance(batch, list):
                raise ValueError("Invalid TWAP list response")
            if batch:
                ids = tuple(row.get('twap', {}).get('twapId') for row in batch if isinstance(row, dict))
                if ids in seen:
                    raise ValueError("TWAP pagination did not advance")
                seen.add(ids)
            rows.extend(batch)
            if len(batch) < 50:
                return parse_active_twaps(rows, trader, self._pair_symbols)
        raise ValueError("TWAP history exceeds bounded scan; status incomplete")

    async def fetch_close_history(self, trader: str) -> list[dict]:
        """Paired close fills in human units; API cache can lag up to five minutes."""
        if not is_valid_address(trader):
            raise ValueError("Invalid trader address")
        base = self.client.config.history_api_url.rstrip('/')
        data = await self.client.transport.json(
            'GET', f'{base}/v2/history/intelligence/trades/{trader}')
        if not isinstance(data, dict) or data.get('success') is not True or not isinstance(data.get('trades'), list):
            raise ValueError('Invalid close-history response')
        return data['trades']

    async def aclose(self) -> None:
        await self._stop_lazer_stream()
        await self._client.aclose()

    async def ensure_pairs(self, *, force: bool = False) -> dict[int, str]:
        now = time.time()
        if (
            self._pair_symbols
            and self._pair_info
            and not force
            and now - self._pairs_loaded_at < _PAIR_CACHE_TTL_S
        ):
            return self._pair_symbols
        pairs = await self._client.markets.pairs()
        mapping = {
            idx: f"{info.from_symbol}/{info.to_symbol}" for idx, info in pairs.items()
        }
        if mapping:
            self._pair_symbols = mapping
            self._pair_info = dict(pairs)
            self._pairs_loaded_at = now
        return self._pair_symbols

    async def get_pair_info(self, pair_index: int) -> PairInfo | None:
        """Return cached PairInfo; refresh catalog / fetch single pair if needed."""
        await self.ensure_pairs()
        info = self._pair_info.get(pair_index)
        if info is not None:
            return info
        try:
            info = await self._client.markets.pair(pair_index)
        except Exception:  # noqa: BLE001
            logger.warning("markets.pair(%s) failed", pair_index, exc_info=True)
            return None
        self._pair_info[pair_index] = info
        self._pair_symbols.setdefault(
            pair_index, f"{info.from_symbol}/{info.to_symbol}"
        )
        return info

    def _lazer_feed_map(self, pair_indexes: set[int]) -> dict[int, list[int]]:
        """lazer feed_id -> pair indexes for the given pairs."""
        feed_to_pair: dict[int, list[int]] = {}
        for idx in pair_indexes:
            info = self._pair_info.get(idx)
            lazer = getattr(info, "lazer_feed", None) if info else None
            fid = getattr(lazer, "feed_id", None) if lazer else None
            if fid is None:
                continue
            try:
                feed_to_pair.setdefault(int(fid), []).append(idx)
            except (TypeError, ValueError):
                continue
        return feed_to_pair

    def _read_lazer_cache(
        self, pair_indexes: set[int]
    ) -> dict[int, tuple[float, float]]:
        """Return still-fresh cached Lazer marks: {pair: (price, age_s)}."""
        import time as _time

        now_mono = _time.monotonic()
        out: dict[int, tuple[float, float]] = {}
        for idx in pair_indexes:
            entry = self._lazer_marks.get(idx)
            if not entry:
                continue
            price, age_at_rx, mono_rx = entry
            wall_age = age_at_rx + (now_mono - mono_rx)
            # Also require the cache sample itself is recent (hot-poll friendly).
            sample_age = now_mono - mono_rx
            if wall_age > self._mark_max_age_s or sample_age > 45.0:
                continue
            if price <= 0:
                continue
            out[idx] = (price, wall_age)
        return out

    def _store_lazer_mark(self, pair_idx: int, price: float, age: float) -> None:
        import time as _time

        if price <= 0 or age > self._mark_max_age_s:
            return
        now_mono = _time.monotonic()
        self._lazer_marks[pair_idx] = (price, age, now_mono)
        try:
            from .ticks import record_tick

            wall_ts = _time.time() - float(age or 0.0)
            record_tick(int(pair_idx), float(price), wall_ts)
        except Exception:  # noqa: BLE001
            logger.debug("lazer tick record failed pair=%s", pair_idx, exc_info=True)

    async def _stop_lazer_stream(self) -> None:
        import asyncio

        stream = self._lazer_stream
        task = self._lazer_task
        self._lazer_stream = None
        self._lazer_task = None
        self._lazer_subscribed_pairs = frozenset()
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001
                pass
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _ensure_lazer_stream(self, pair_indexes: set[int]) -> None:
        """Keep a background Lazer SSE updating the in-memory mark cache."""
        import asyncio

        if not pair_indexes:
            return
        await self.ensure_pairs()
        wanted = frozenset(pair_indexes)
        task = self._lazer_task
        if (
            wanted <= self._lazer_subscribed_pairs
            and task is not None
            and not task.done()
        ):
            return

        cover = set(self._lazer_subscribed_pairs) | set(pair_indexes)
        feed_to_pair = self._lazer_feed_map(cover)
        if not feed_to_pair:
            return

        await self._stop_lazer_stream()
        stream = self._client.lazer_price_stream(list(feed_to_pair.keys()))
        self._lazer_stream = stream
        self._lazer_subscribed_pairs = frozenset(i for indexes in feed_to_pair.values() for i in indexes)

        async def on_price(update: Any) -> None:
            try:
                fid = int(update.feed_id)
            except (TypeError, ValueError):
                return
            pair_indexes_for_feed = feed_to_pair.get(fid, [])
            if not pair_indexes_for_feed:
                return
            price = _f(getattr(update, "price", None))
            if price <= 0:
                return
            ts_ms = getattr(update, "timestamp_ms", None)
            if ts_ms is not None:
                try:
                    age = (time.time() * 1000.0 - float(ts_ms)) / 1000.0
                except (TypeError, ValueError):
                    age = 0.0
            else:
                age = 0.0
            if age > self._mark_max_age_s:
                return
            for pair_idx in pair_indexes_for_feed:
                self._store_lazer_mark(pair_idx, price, age)

        async def runner() -> None:
            try:
                await stream.run(on_price)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("Lazer background stream ended", exc_info=True)
            finally:
                try:
                    stream.stop()
                except Exception:  # noqa: BLE001
                    pass

        self._lazer_task = asyncio.create_task(runner(), name="veranta-lazer-marks")
        logger.info(
            "Lazer mark stream started for %d pair(s): %s",
            len(self._lazer_subscribed_pairs),
            sorted(self._lazer_subscribed_pairs),
        )

    def _pyth_id_by_pair(self) -> dict[int, str]:
        """pair_index -> normalized 64-hex Pyth/Hermes feed id (no 0x)."""
        out: dict[int, str] = {}
        for idx, info in self._pair_info.items():
            feed = getattr(info, "feed", None)
            fid = _norm_pyth_id(getattr(feed, "feed_id", None) if feed else None)
            if fid:
                out[idx] = fid
        return out

    async def _fetch_hermes_marks(
        self, pair_indexes: set[int] | None = None
    ) -> dict[int, tuple[float, float]]:
        """Hermes HTTP latest → {pair_index: (price, age_seconds)}."""
        await self.ensure_pairs()
        id_by_pair = self._pyth_id_by_pair()
        if pair_indexes is not None:
            id_by_pair = {k: v for k, v in id_by_pair.items() if k in pair_indexes}
        if not id_by_pair:
            return {}

        # Reverse map for response ids (Hermes often omits 0x).
        pair_by_id = {fid: idx for idx, fid in id_by_pair.items()}
        # Batch request; hermes accepts repeated ids[]=
        ids = sorted(set(id_by_pair.values()))
        query = urlencode([("ids[]", f"0x{fid}") for fid in ids])
        url = f"{self._hermes_rest_url}?{query}"
        now = time.time()
        try:
            async with httpx.AsyncClient(timeout=12.0) as http:
                resp = await http.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception:  # noqa: BLE001
            logger.warning("Hermes latest-price fetch failed; will try fallbacks", exc_info=True)
            return {}

        out: dict[int, tuple[float, float]] = {}
        for item in data.get("parsed") or []:
            fid = _norm_pyth_id(item.get("id"))
            if not fid or fid not in pair_by_id:
                continue
            price_obj = item.get("price") or {}
            try:
                raw = float(price_obj["price"])
                expo = int(price_obj["expo"])
                publish = float(price_obj["publish_time"])
                price = raw * (10**expo)
            except (KeyError, TypeError, ValueError):
                continue
            age = now - publish
            if age > self._mark_max_age_s:
                logger.warning(
                    "rejecting stale Hermes mark pair_index=%s age_s=%.1f max_age_s=%.1f",
                    pair_by_id[fid],
                    age,
                    self._mark_max_age_s,
                )
                continue
            if price <= 0:
                continue
            out[pair_by_id[fid]] = (price, age)
        return out

    async def fetch_feed_v3_last_prices(
        self,
    ) -> dict[int, tuple[float, float | None]]:
        """Raw feed-v3 last-price → {pair_index: (price, age_seconds|None)}.

        Does **not** filter by staleness — callers decide. Age uses row `t`
        (ms if > 1e12, else seconds).
        """
        feed = self._client.config.feed_url.rstrip("/")
        data = await self._client.transport.json(
            "GET", f"{feed}/v1/price-feeds/last-price"
        )
        rows = data if isinstance(data, list) else (data or {}).get("data", [])
        now = time.time()
        out: dict[int, tuple[float, float | None]] = {}
        for row in rows or []:
            try:
                idx = int(row["pairIndex"])
                price = float(row["c"])
            except (KeyError, TypeError, ValueError):
                continue
            age: float | None = None
            t_raw = row.get("t")
            if t_raw is not None:
                try:
                    t = float(t_raw)
                    age = (now - t / 1000.0) if t > 1e12 else (now - t)
                except (TypeError, ValueError):
                    age = None
            out[idx] = (price, age)
        return out

    async def _fetch_lazer_oneshot(
        self, pair_indexes: set[int], *, timeout_s: float = 6.0
    ) -> dict[int, tuple[float, float]]:
        """Briefly sample lazer_price_stream for one update per feed id."""
        await self.ensure_pairs()
        feed_to_pair = self._lazer_feed_map(pair_indexes)
        if not feed_to_pair:
            return {}

        stream = self._client.lazer_price_stream(list(feed_to_pair.keys()))
        got: dict[int, tuple[float, float]] = {}
        now0 = time.time()

        async def on_price(update: Any) -> None:
            try:
                fid = int(update.feed_id)
            except (TypeError, ValueError):
                return
            pair_indexes_for_feed = feed_to_pair.get(fid, [])
            if not pair_indexes_for_feed or all(i in got for i in pair_indexes_for_feed):
                return
            price = _f(getattr(update, "price", None))
            if price <= 0:
                return
            ts_ms = getattr(update, "timestamp_ms", None)
            if ts_ms is not None:
                try:
                    age = (time.time() * 1000.0 - float(ts_ms)) / 1000.0
                except (TypeError, ValueError):
                    age = time.time() - now0
            else:
                age = time.time() - now0
            if age > self._mark_max_age_s:
                logger.warning(
                    "rejecting stale Lazer mark pair_index=%s age_s=%.1f",
                    pair_indexes_for_feed,
                    age,
                )
                return
            for pair_idx in pair_indexes_for_feed:
                got[pair_idx] = (price, age)
                self._store_lazer_mark(pair_idx, price, age)
            if len(got) >= sum(len(v) for v in feed_to_pair.values()):
                stream.stop()

        try:
            await asyncio_wait_for_stream(stream, on_price, timeout_s)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Lazer one-shot sample failed/timed out (got %d/%d)",
                len(got),
                len(feed_to_pair),
                exc_info=True,
            )
        return got

    async def _apply_fresh_feed_v3(
        self,
        pair_indexes: set[int],
        out: dict[int, float],
        source: dict[int, str],
    ) -> None:
        """Fill missing marks from feed-v3 only when age ≤ MARK_MAX_AGE_SECONDS."""
        still = pair_indexes - set(out)
        if not still:
            return
        try:
            feed_rows = await self.fetch_feed_v3_last_prices()
        except Exception:  # noqa: BLE001
            logger.warning("feed-v3 last-price fetch failed", exc_info=True)
            return

        stale_rejected = 0
        for idx in list(still):
            row = feed_rows.get(idx)
            if not row:
                continue
            price, age = row
            if age is None:
                logger.warning(
                    "rejecting feed-v3 mark pair_index=%s (missing timestamp)", idx
                )
                stale_rejected += 1
                continue
            if age > self._mark_max_age_s:
                stale_rejected += 1
                continue
            if price <= 0:
                continue
            out[idx] = price
            source[idx] = f"feed-v3 age_s={age:.1f}"
            logger.info(
                "mark source=feed-v3 pair_index=%s age_s=%.1f price=%.6f",
                idx,
                age,
                price,
            )

        if stale_rejected:
            btc = feed_rows.get(1)
            example = ""
            if btc and btc[1] is not None:
                example = f" (e.g. BTC/USD feed-v3 age_s={btc[1]:.0f} c={btc[0]})"
            logger.warning(
                "rejected %d stale feed-v3 mark(s) (max_age_s=%.0f)%s",
                stale_rejected,
                self._mark_max_age_s,
                example,
            )

    async def fetch_mark_prices(self) -> dict[int, float]:
        """Live marks for PnL/alerts (Veranta feeds).

        Preference order per pair:
          1) Veranta Lazer SSE (`pair.lazer_feed.feed_id`) — cache / one-shot
          2) feed-v3 last-price **only if** age ≤ MARK_MAX_AGE_SECONDS

        Hermes is intentionally omitted so marks come from Veranta-native feeds.
        Stale feed-v3 rows are rejected (historically ~25h behind).
        """
        await self.ensure_pairs()
        # Full-catalog path is rare; prefer Lazer for a small sample of pairs.
        targets = set(self._pair_info.keys())
        if len(targets) > 8:
            # Cap oneshot work; callers with open positions use for_pairs.
            targets = set(list(targets)[:8])
        return await self.fetch_mark_prices_for_pairs(targets)

    async def fetch_mark_prices_for_pairs(
        self, pair_indexes: set[int]
    ) -> dict[int, float]:
        """Resolve marks for specific pairs via Veranta Lazer, then fresh feed-v3."""
        if not pair_indexes:
            self._last_mark_sources = {}
            return {}
        await self.ensure_pairs()
        out: dict[int, float] = {}
        source: dict[int, str] = {}

        # Keep background Lazer stream warm for the hot 15s poll path.
        try:
            await self._ensure_lazer_stream(pair_indexes)
        except Exception:  # noqa: BLE001
            logger.warning("failed starting Lazer mark stream", exc_info=True)

        # 1) Fresh Lazer cache (background stream or prior one-shot)
        cached = self._read_lazer_cache(pair_indexes)
        for idx, (price, age) in cached.items():
            out[idx] = price
            source[idx] = f"lazer age_s={age:.1f}"
            logger.info(
                "mark source=lazer pair_index=%s age_s=%.1f price=%.6f (cache)",
                idx,
                age,
                price,
            )

        still = pair_indexes - set(out)
        if still:
            # Brief wait for the background stream to populate, then oneshot gaps.
            import asyncio

            for _ in range(8):  # ~1.6s
                await asyncio.sleep(0.2)
                warmed = self._read_lazer_cache(still)
                for idx, (price, age) in warmed.items():
                    out[idx] = price
                    source[idx] = f"lazer age_s={age:.1f}"
                    logger.info(
                        "mark source=lazer pair_index=%s age_s=%.1f price=%.6f (stream)",
                        idx,
                        age,
                        price,
                    )
                still = pair_indexes - set(out)
                if not still:
                    break

        if still:
            lazer = await self._fetch_lazer_oneshot(still, timeout_s=5.0)
            for idx, (price, age) in lazer.items():
                out[idx] = price
                source[idx] = f"lazer age_s={age:.1f}"
                logger.info(
                    "mark source=lazer pair_index=%s age_s=%.1f price=%.6f (oneshot)",
                    idx,
                    age,
                    price,
                )

        # 2) Secondary: fresh feed-v3 only
        await self._apply_fresh_feed_v3(pair_indexes, out, source)

        self._last_mark_sources = source
        missing = pair_indexes - set(out)
        if missing:
            logger.warning(
                "no Veranta mark for pair_index(es)=%s after lazer+feed-v3",
                sorted(missing),
            )
        return out

    def chart_reference_symbol(self, pair_index):
        info = self._pair_info.get(pair_index)
        feed_id = getattr(getattr(info, "lazer_feed", None), "feed_id", None)
        if feed_id is None:
            return None
        for idx, other in self._pair_info.items():
            if getattr(getattr(other, "lazer_feed", None), "feed_id", None) == feed_id:
                symbol = self._pair_symbols.get(idx, "")
                if "UPSIDE" not in symbol and "DOWNSIDE" not in symbol:
                    return symbol
        return None

    async def fetch_positions(self, trader: str) -> list[Position]:
        """Read open positions for any Base address via SDK account.positions(trader=)."""
        trader = normalize_address(trader)
        await self.ensure_pairs()
        data = await self._client.account.positions(trader=trader)
        needed = {int(pos.pair_index) for pos in data.positions}
        # Veranta-native marks (Lazer primary) for open pairs only.
        marks = await self.fetch_mark_prices_for_pairs(needed) if needed else {}
        out: list[Position] = []
        for pos in data.positions:
            pair_index = int(pos.pair_index)
            symbol = self._pair_symbols.get(pair_index)
            if not symbol and pos.base_symbol:
                symbol = f"{pos.base_symbol}/USD"
            if not symbol:
                symbol = f"PAIR#{pair_index}"
            mark = marks.get(pair_index)
            if mark is None:
                logger.warning(
                    "no fresh mark for pair_index=%s symbol=%s; PnL omitted",
                    pair_index,
                    symbol,
                )
            pair_info = self._pair_info.get(pair_index)
            if pair_info is None:
                pair_info = await self.get_pair_info(pair_index)
            net_usdc, gross_usdc = _pnl_for_sdk_position(pos, pair_info, mark)
            collateral = _f(pos.collateral)
            net_pct = (
                (net_usdc / collateral * 100.0)
                if net_usdc is not None and collateral > 0
                else None
            )
            out.append(
                Position(
                    trader=str(pos.trader or trader),
                    pair_index=pair_index,
                    index=int(pos.index),
                    is_long=bool(pos.buy),
                    collateral_usdc=collateral,
                    leverage=_f(pos.leverage),
                    open_price=_f(pos.open_price),
                    tp=_f(pos.tp),
                    sl=_f(pos.sl),
                    liquidation_price=_f(pos.liquidation_price),
                    mark_price=mark,
                    symbol=symbol,
                    opened_at=int(pos.opened_at) if pos.opened_at else None,
                    unrealized_pnl_usdc=net_usdc,
                    unrealized_pnl_pct=net_pct,
                    gross_pnl_usdc=gross_usdc,
                    chart_symbol=self.chart_reference_symbol(pair_index),
                )
            )
        return out


async def asyncio_wait_for_stream(stream: Any, callback: Any, timeout_s: float) -> None:
    """Run stream.callback until stop or timeout; always stop the stream."""
    import asyncio

    try:
        await asyncio.wait_for(stream.run(callback), timeout=timeout_s)
    except asyncio.TimeoutError:
        stream.stop()
        raise
    finally:
        try:
            stream.stop()
        except Exception:  # noqa: BLE001
            pass
