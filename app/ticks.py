"""Persistent Veranta Lazer mark tape (last ~2h per pair_index).

The running bot records ticks from the Lazer SSE callback. Chart rendering
reads them synchronously (sqlite) so the Pillow ticket can draw a sparkline
of *Veranta* marks — never Binance spot.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.getenv("SQLITE_PATH", str(ROOT / "data" / "bot.db"))).parent / "lazer_ticks.db"

RETENTION_S = 2 * 3600.0
MIN_INTERVAL_S = 2.0
MAX_POINTS_READ = 720
_PRUNE_EVERY_WRITES = 80

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_db_path: Path = DEFAULT_DB
_last_write: dict[int, tuple[float, float]] = {}  # pair -> (ts, price)
_writes_since_prune = 0


def configure(path: Path | None = None) -> None:
    """Point the tape at a different sqlite file (tests / sidecar)."""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None
        if path is not None:
            _db_path = Path(path)


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(_db_path),
            check_same_thread=False,
            timeout=8.0,
            isolation_level=None,  # autocommit
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticks (
                pair_index INTEGER NOT NULL,
                ts REAL NOT NULL,
                price REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ticks_pair_ts ON ticks(pair_index, ts)"
        )
        _conn = conn
    return _conn


def record_tick(
    pair_index: int,
    price: float,
    ts: float | None = None,
) -> bool:
    """Append a Lazer mark. Returns True if a row was written."""
    global _writes_since_prune
    try:
        idx = int(pair_index)
        px = float(price)
    except (TypeError, ValueError):
        return False
    if px <= 0:
        return False
    now = float(ts) if ts is not None else time.time()
    if now <= 0:
        now = time.time()

    with _lock:
        last = _last_write.get(idx)
        if last is not None:
            dt = now - last[0]
            if dt < MIN_INTERVAL_S:
                # Keep the latest price in the window without flooding sqlite.
                return False
            if last[1] > 0 and abs(px - last[1]) / last[1] < 1e-9 and dt < 8.0:
                return False
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO ticks (pair_index, ts, price) VALUES (?, ?, ?)",
                (idx, now, px),
            )
        except Exception:  # noqa: BLE001
            logger.debug("lazer tick insert failed pair=%s", idx, exc_info=True)
            return False
        _last_write[idx] = (now, px)
        _writes_since_prune += 1
        if _writes_since_prune >= _PRUNE_EVERY_WRITES:
            _writes_since_prune = 0
            _prune_unlocked(RETENTION_S)
    return True


def load_ticks(
    pair_index: int,
    *,
    hours: float = 2.0,
    limit: int = MAX_POINTS_READ,
) -> list[tuple[float, float]]:
    """Return ``[(ts, price), ...]`` oldest-first for the last ``hours``."""
    try:
        idx = int(pair_index)
    except (TypeError, ValueError):
        return []
    cutoff = time.time() - max(60.0, float(hours) * 3600.0)
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute(
                """
                SELECT ts, price FROM ticks
                WHERE pair_index = ? AND ts >= ? AND price > 0
                ORDER BY ts ASC
                """,
                (idx, cutoff),
            ).fetchall()
        except Exception:  # noqa: BLE001
            logger.debug("lazer tick load failed pair=%s", idx, exc_info=True)
            return []
    if not rows:
        return []
    if limit and len(rows) > int(limit):
        # Even stride, keep first + last.
        n = int(limit)
        step = (len(rows) - 1) / (n - 1)
        picked = [rows[int(round(i * step))] for i in range(n - 1)]
        picked.append(rows[-1])
        rows = picked
    out: list[tuple[float, float]] = []
    for ts, price in rows:
        try:
            out.append((float(ts), float(price)))
        except (TypeError, ValueError):
            continue
    return out


def tick_count(pair_index: int, *, hours: float = 2.0) -> int:
    try:
        idx = int(pair_index)
    except (TypeError, ValueError):
        return 0
    cutoff = time.time() - max(60.0, float(hours) * 3600.0)
    with _lock:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT COUNT(*) FROM ticks WHERE pair_index = ? AND ts >= ?",
                (idx, cutoff),
            ).fetchone()
        except Exception:  # noqa: BLE001
            return 0
    return int(row[0]) if row else 0


def prune(older_than_s: float = RETENTION_S) -> int:
    with _lock:
        return _prune_unlocked(older_than_s)


def _prune_unlocked(older_than_s: float) -> int:
    cutoff = time.time() - float(older_than_s)
    try:
        conn = _get_conn()
        cur = conn.execute("DELETE FROM ticks WHERE ts < ?", (cutoff,))
        n = cur.rowcount if cur.rowcount is not None else 0
        if n:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            logger.info("pruned %d lazer ticks older than %.0fs", n, older_than_s)
        return int(n)
    except Exception:  # noqa: BLE001
        logger.debug("lazer tick prune failed", exc_info=True)
        return 0


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None
