"""SQLite persistence for subscriptions, hot/idle, alert hysteresis, prefs."""

from __future__ import annotations

import time
import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import aiosqlite

from .prefs import (
    DEFAULT_LIQ_LEVELS,
    DEFAULT_SL_DISTANCE_PNL,
    DEFAULT_TP_DISTANCE_PNL,
    AlertPrefs,
    default_prefs,
    liq_levels_from_json,
    liq_levels_to_json,
    normalize_liq_levels,
)


@dataclass
class Subscription:
    chat_id: int
    wallet: str
    muted: bool
    is_hot: bool
    created_at: float
    updated_at: float


def serialized(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self.transaction():
            return await method(self, *args, **kwargs)
    return wrapped


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._owner = None
        self._db: aiosqlite.Connection | None = None

    @asynccontextmanager
    async def transaction(self):
        task = asyncio.current_task()
        if self._owner is task:
            yield
            return
        async with self._lock:
            self._owner = task
            try:
                await self.db.execute("BEGIN IMMEDIATE")
                yield
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise
            finally:
                self._owner = None

    async def _commit(self):
        if self._owner is not asyncio.current_task():
            await self.db.commit()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at REAL NOT NULL,
                next_attempt REAL NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                chat_id INTEGER PRIMARY KEY,
                wallet TEXT NOT NULL,
                muted INTEGER NOT NULL DEFAULT 0,
                is_hot INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_state (
                chat_id INTEGER NOT NULL,
                position_key TEXT NOT NULL,
                level TEXT NOT NULL,
                last_fired_at REAL NOT NULL DEFAULT 0,
                armed INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (chat_id, position_key, level)
            );

            CREATE TABLE IF NOT EXISTS position_snapshots (
                chat_id INTEGER NOT NULL,
                position_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (chat_id, position_key)
            );

            CREATE TABLE IF NOT EXISTS alert_prefs (
                chat_id INTEGER PRIMARY KEY,
                liq_enabled INTEGER NOT NULL DEFAULT 1,
                tp_enabled INTEGER NOT NULL DEFAULT 1,
                sl_enabled INTEGER NOT NULL DEFAULT 1,
                liq_levels TEXT NOT NULL,
                tp_distance_pnl REAL NOT NULL DEFAULT 10,
                sl_distance_pnl REAL NOT NULL DEFAULT 10,
                updated_at REAL NOT NULL
            );
            """
        )
        # Migrate older DBs that lack is_hot
        cur = await self._db.execute("PRAGMA table_info(subscriptions)")
        cols = {row[1] for row in await cur.fetchall()}
        if "is_hot" not in cols:
            await self._db.execute(
                "ALTER TABLE subscriptions ADD COLUMN is_hot INTEGER NOT NULL DEFAULT 0"
            )
        await self._commit()
        # Ensure every existing subscription has default prefs (safe migrate).
        await self._ensure_prefs_for_all_subs()

    async def _ensure_prefs_for_all_subs(self) -> None:
        cur = await self.db.execute("SELECT chat_id FROM subscriptions")
        rows = await cur.fetchall()
        now = time.time()
        levels_json = liq_levels_to_json(DEFAULT_LIQ_LEVELS)
        for row in rows:
            await self.db.execute(
                """
                INSERT OR IGNORE INTO alert_prefs (
                    chat_id, liq_enabled, tp_enabled, sl_enabled,
                    liq_levels, tp_distance_pnl, sl_distance_pnl, updated_at
                ) VALUES (?, 1, 1, 1, ?, ?, ?, ?)
                """,
                (
                    row["chat_id"],
                    levels_json,
                    DEFAULT_TP_DISTANCE_PNL,
                    DEFAULT_SL_DISTANCE_PNL,
                    now,
                ),
            )
        await self._commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected")
        return self._db

    def _row_to_sub(self, row: aiosqlite.Row) -> Subscription:
        return Subscription(
            chat_id=row["chat_id"],
            wallet=row["wallet"],
            muted=bool(row["muted"]),
            is_hot=bool(row["is_hot"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_prefs(self, row: aiosqlite.Row) -> AlertPrefs:
        return AlertPrefs(
            chat_id=int(row["chat_id"]),
            liq_enabled=bool(row["liq_enabled"]),
            tp_enabled=bool(row["tp_enabled"]),
            sl_enabled=bool(row["sl_enabled"]),
            liq_levels=liq_levels_from_json(row["liq_levels"]),
            tp_distance_pnl=float(row["tp_distance_pnl"]),
            sl_distance_pnl=float(row["sl_distance_pnl"]),
        )

    @serialized
    async def upsert_subscription(self, chat_id: int, wallet: str) -> Subscription:
        previous = await self.get_subscription(chat_id)
        if previous and previous.wallet != wallet.lower():
            for table in ("alert_state", "position_snapshots", "outbox"):
                await self.db.execute(f"DELETE FROM {table} WHERE chat_id=?", (chat_id,))
        now = time.time()
        await self.db.execute(
            """
            INSERT INTO subscriptions (chat_id, wallet, muted, is_hot, created_at, updated_at)
            VALUES (?, ?, 0, 0, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                wallet=excluded.wallet,
                is_hot=CASE WHEN subscriptions.wallet=excluded.wallet THEN subscriptions.is_hot ELSE 0 END,
                muted=0,
                updated_at=excluded.updated_at
            """,
            (chat_id, wallet.lower(), now, now),
        )
        # Create default prefs if missing (do not overwrite custom prefs).
        await self.db.execute(
            """
            INSERT OR IGNORE INTO alert_prefs (
                chat_id, liq_enabled, tp_enabled, sl_enabled,
                liq_levels, tp_distance_pnl, sl_distance_pnl, updated_at
            ) VALUES (?, 1, 1, 1, ?, ?, ?, ?)
            """,
            (
                chat_id,
                liq_levels_to_json(DEFAULT_LIQ_LEVELS),
                DEFAULT_TP_DISTANCE_PNL,
                DEFAULT_SL_DISTANCE_PNL,
                now,
            ),
        )
        await self._commit()
        sub = await self.get_subscription(chat_id)
        assert sub is not None
        return sub

    @serialized
    async def get_subscription(self, chat_id: int) -> Subscription | None:
        cur = await self.db.execute(
            "SELECT chat_id, wallet, muted, is_hot, created_at, updated_at "
            "FROM subscriptions WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cur.fetchone()
        return self._row_to_sub(row) if row else None

    @serialized
    async def set_muted(self, chat_id: int, muted: bool) -> bool:
        if muted:
            await self.db.execute("DELETE FROM outbox WHERE chat_id=?", (chat_id,))
        cur = await self.db.execute(
            "UPDATE subscriptions SET muted = ?, updated_at = ? WHERE chat_id = ?",
            (1 if muted else 0, time.time(), chat_id),
        )
        await self._commit()
        return cur.rowcount > 0

    @serialized
    async def set_hot(self, chat_id: int, is_hot: bool) -> None:
        await self.db.execute(
            "UPDATE subscriptions SET is_hot = ?, updated_at = ? WHERE chat_id = ?",
            (1 if is_hot else 0, time.time(), chat_id),
        )
        await self._commit()

    @serialized
    async def delete_subscription(self, chat_id: int) -> bool:
        await self.db.execute("DELETE FROM outbox WHERE chat_id=?", (chat_id,))
        await self.db.execute(
            "DELETE FROM alert_state WHERE chat_id = ?", (chat_id,)
        )
        await self.db.execute(
            "DELETE FROM position_snapshots WHERE chat_id = ?", (chat_id,)
        )
        await self.db.execute(
            "DELETE FROM alert_prefs WHERE chat_id = ?", (chat_id,)
        )
        cur = await self.db.execute(
            "DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,)
        )
        await self._commit()
        return cur.rowcount > 0

    @serialized
    async def list_subscriptions(self, *, hot: bool | None = None) -> list[Subscription]:
        if hot is None:
            cur = await self.db.execute(
                "SELECT chat_id, wallet, muted, is_hot, created_at, updated_at "
                "FROM subscriptions"
            )
        else:
            cur = await self.db.execute(
                "SELECT chat_id, wallet, muted, is_hot, created_at, updated_at "
                "FROM subscriptions WHERE is_hot = ?",
                (1 if hot else 0,),
            )
        rows = await cur.fetchall()
        return [self._row_to_sub(r) for r in rows]

    # --- alert prefs --------------------------------------------------------

    @serialized
    async def get_alert_prefs(self, chat_id: int) -> AlertPrefs:
        cur = await self.db.execute(
            "SELECT chat_id, liq_enabled, tp_enabled, sl_enabled, "
            "liq_levels, tp_distance_pnl, sl_distance_pnl "
            "FROM alert_prefs WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cur.fetchone()
        if not row:
            return default_prefs(chat_id)
        return self._row_to_prefs(row)

    @serialized
    async def save_alert_prefs(self, prefs: AlertPrefs) -> AlertPrefs:
        now = time.time()
        levels = normalize_liq_levels(prefs.liq_levels)
        await self.db.execute(
            """
            INSERT INTO alert_prefs (
                chat_id, liq_enabled, tp_enabled, sl_enabled,
                liq_levels, tp_distance_pnl, sl_distance_pnl, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                liq_enabled=excluded.liq_enabled,
                tp_enabled=excluded.tp_enabled,
                sl_enabled=excluded.sl_enabled,
                liq_levels=excluded.liq_levels,
                tp_distance_pnl=excluded.tp_distance_pnl,
                sl_distance_pnl=excluded.sl_distance_pnl,
                updated_at=excluded.updated_at
            """,
            (
                prefs.chat_id,
                1 if prefs.liq_enabled else 0,
                1 if prefs.tp_enabled else 0,
                1 if prefs.sl_enabled else 0,
                liq_levels_to_json(levels),
                float(prefs.tp_distance_pnl),
                float(prefs.sl_distance_pnl),
                now,
            ),
        )
        await self._commit()
        return await self.get_alert_prefs(prefs.chat_id)

    @serialized
    async def ensure_alert_prefs(self, chat_id: int) -> AlertPrefs:
        """Return prefs, inserting defaults if the chat has no row yet."""
        prefs = await self.get_alert_prefs(chat_id)
        cur = await self.db.execute(
            "SELECT 1 FROM alert_prefs WHERE chat_id = ?", (chat_id,)
        )
        if await cur.fetchone() is None:
            return await self.save_alert_prefs(prefs)
        return prefs

    # --- alert state --------------------------------------------------------

    @serialized
    async def get_alert_state(
        self, chat_id: int, position_key: str, level: str
    ) -> tuple[float, bool]:
        cur = await self.db.execute(
            "SELECT last_fired_at, armed FROM alert_state "
            "WHERE chat_id=? AND position_key=? AND level=?",
            (chat_id, position_key, level),
        )
        row = await cur.fetchone()
        if not row:
            return 0.0, True
        return float(row["last_fired_at"]), bool(row["armed"])

    @serialized
    async def set_alert_state(
        self,
        chat_id: int,
        position_key: str,
        level: str,
        *,
        last_fired_at: float | None = None,
        armed: bool | None = None,
    ) -> None:
        prev_fired, prev_armed = await self.get_alert_state(chat_id, position_key, level)
        fired = prev_fired if last_fired_at is None else last_fired_at
        is_armed = prev_armed if armed is None else armed
        await self.db.execute(
            """
            INSERT INTO alert_state (chat_id, position_key, level, last_fired_at, armed)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, position_key, level) DO UPDATE SET
                last_fired_at=excluded.last_fired_at,
                armed=excluded.armed
            """,
            (chat_id, position_key, level, fired, 1 if is_armed else 0),
        )
        await self._commit()

    @serialized
    async def clear_alert_states(self, chat_id: int, position_keys: Iterable[str]) -> None:
        keys = list(position_keys)
        if not keys:
            await self.db.execute(
                "DELETE FROM alert_state WHERE chat_id=?", (chat_id,)
            )
            await self._commit()
            return
        placeholders = ",".join("?" for _ in keys)
        await self.db.execute(
            f"DELETE FROM alert_state WHERE chat_id=? AND position_key NOT IN ({placeholders})",
            (chat_id, *keys),
        )
        await self._commit()

    @serialized
    async def save_position_snapshot(
        self, chat_id: int, position_key: str, payload_json: str
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO position_snapshots (chat_id, position_key, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, position_key) DO UPDATE SET
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (chat_id, position_key, payload_json, time.time()),
        )
        await self._commit()

    @serialized
    async def list_position_snapshots(self, chat_id: int) -> dict[str, str]:
        cur = await self.db.execute(
            "SELECT position_key, payload_json FROM position_snapshots WHERE chat_id=?",
            (chat_id,),
        )
        rows = await cur.fetchall()
        return {r["position_key"]: r["payload_json"] for r in rows}

    @serialized
    async def delete_position_snapshots(
        self, chat_id: int, position_keys: Iterable[str]
    ) -> None:
        for key in position_keys:
            await self.db.execute(
                "DELETE FROM position_snapshots WHERE chat_id=? AND position_key=?",
                (chat_id, key),
            )
        await self._commit()

    @serialized
    async def enqueue_alert(self, chat_id: int, text: str, kind: str) -> None:
        await self.db.execute(
            "INSERT INTO outbox(chat_id,text,kind,created_at) VALUES (?,?,?,?)",
            (chat_id, text, kind, time.time()),
        )

    @serialized
    async def pending_alerts(self, *, include_delayed=False):
        query = "SELECT * FROM outbox"
        params = ()
        if not include_delayed:
            query += " WHERE next_attempt <= ?"
            params = (time.time(),)
        cur = await self.db.execute(query + " ORDER BY id LIMIT 100", params)
        return [dict(row) for row in await cur.fetchall()]

    @serialized
    async def acknowledge_alert(self, alert_id: int):
        await self.db.execute("DELETE FROM outbox WHERE id=?", (alert_id,))

    @serialized
    async def retry_alert(self, alert_id: int, delay: float):
        await self.db.execute(
            "UPDATE outbox SET attempts=attempts+1,next_attempt=? WHERE id=?",
            (time.time()+delay, alert_id),
        )
