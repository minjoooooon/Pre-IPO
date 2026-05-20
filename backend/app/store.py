"""SQLite append-only tick persistence.

Stores normalized quotes for 24h replay on restart and 7-day rolling retention.
Uses WAL mode + batched inserts for safe concurrent reads."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import aiosqlite
import structlog

from .config import settings
from .models import NormalizedQuote

log = structlog.get_logger("app.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    market_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    exchange TEXT NOT NULL,
    token_price REAL NOT NULL,
    implied_share_price REAL,
    implied_fdv REAL,
    vol24 REAL,
    oi REAL,
    funding_rate REAL,
    confidence INTEGER NOT NULL,
    calibration_status TEXT NOT NULL,
    timestamp REAL NOT NULL,
    PRIMARY KEY (market_id, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_asset_time ON ticks (asset_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_market_time ON ticks (market_id, timestamp DESC);
"""


class TickStore:
    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None
        # aiosqlite serialises ops through a single thread, but we still wrap
        # every write in this lock so an `executemany` + `commit` pair cannot
        # be interleaved with another writer's `execute` + `commit` and share
        # an implicit transaction.
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        # Coerce to str: aiosqlite/sqlite3 historically wanted str, and Windows
        # paths with non-ASCII / spaces can trip Path passthrough on some builds.
        self._db = await aiosqlite.connect(str(settings.db_path))
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        for stmt in SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                await self._db.execute(stmt)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def insert_batch(self, quotes: Iterable[NormalizedQuote]) -> int:
        if self._db is None:
            raise RuntimeError("TickStore not initialized")
        rows = [
            (
                f"{q.asset_id}:{q.exchange}",
                q.asset_id,
                q.exchange,
                q.token_price,
                q.implied_share_price,
                q.implied_fdv,
                q.vol24,
                q.oi,
                q.funding_rate,
                q.confidence,
                q.calibration_status,
                q.timestamp,
            )
            for q in quotes
        ]
        if not rows:
            return 0
        async with self._write_lock:
            async with self._db.cursor() as cur:
                await cur.executemany(
                    """
                    INSERT OR IGNORE INTO ticks
                      (market_id, asset_id, exchange, token_price, implied_share_price, implied_fdv,
                       vol24, oi, funding_rate, confidence, calibration_status, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                inserted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(rows)
            await self._db.commit()
        if inserted < len(rows):
            # Duplicate (market_id, timestamp) collisions — exchange re-emitted
            # the same ts, or poll loop returned an unchanged frame.
            log.debug("store_dupe_ts_dropped", attempted=len(rows), inserted=inserted)
        return inserted

    async def replay_recent(
        self, asset_id: str, seconds: int, limit: int | None = None
    ) -> list[dict]:
        """Return ticks for an asset newer than (now - seconds), oldest first.

        `limit` (optional) caps the number of rows returned by the SQLite
        engine itself — picks the NEWEST rows when the window contains more
        than `limit` ticks. None means no SQL-level cap (warm-up path)."""
        if self._db is None:
            raise RuntimeError("TickStore not initialized")
        cutoff = time.time() - seconds
        if limit is None:
            query = (
                "SELECT market_id, asset_id, exchange, token_price, implied_share_price, implied_fdv, "
                "       vol24, oi, funding_rate, confidence, calibration_status, timestamp "
                "FROM ticks "
                "WHERE asset_id = ? AND timestamp > ? "
                "ORDER BY timestamp ASC"
            )
            params: tuple = (asset_id, cutoff)
        else:
            # Two-step: take newest `limit` then re-order ASC so the caller
            # sees oldest-first like the unbounded path.
            query = (
                "SELECT * FROM ("
                "  SELECT market_id, asset_id, exchange, token_price, implied_share_price, implied_fdv, "
                "         vol24, oi, funding_rate, confidence, calibration_status, timestamp "
                "  FROM ticks "
                "  WHERE asset_id = ? AND timestamp > ? "
                "  ORDER BY timestamp DESC LIMIT ?"
                ") ORDER BY timestamp ASC"
            )
            params = (asset_id, cutoff, limit)
        cursor = await self._db.execute(query, params)
        cols = [c[0] for c in cursor.description]
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(zip(cols, row, strict=True)) for row in rows]

    async def cleanup_older_than(self, days: int) -> int:
        if self._db is None:
            raise RuntimeError("TickStore not initialized")
        cutoff = time.time() - days * 86_400
        async with self._write_lock:
            cursor = await self._db.execute("DELETE FROM ticks WHERE timestamp < ?", (cutoff,))
            await self._db.commit()
            rowcount = cursor.rowcount or 0
            await cursor.close()
        return rowcount


_store: TickStore | None = None


def get_store() -> TickStore:
    global _store
    if _store is None:
        _store = TickStore()
    return _store
