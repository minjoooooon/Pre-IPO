"""SQLite append-only tick persistence.

Stores normalized quotes for 24h replay on restart and 7-day rolling retention.
Uses WAL mode + batched inserts for safe concurrent reads."""

from __future__ import annotations

import time
from collections.abc import Iterable

import aiosqlite

from .config import settings
from .models import NormalizedQuote

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

    async def init(self) -> None:
        self._db = await aiosqlite.connect(settings.db_path)
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
        await self._db.executemany(
            """
            INSERT OR IGNORE INTO ticks
              (market_id, asset_id, exchange, token_price, implied_share_price, implied_fdv,
               vol24, oi, funding_rate, confidence, calibration_status, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await self._db.commit()
        return len(rows)

    async def replay_recent(self, asset_id: str, seconds: int) -> list[dict]:
        """Return ticks for an asset newer than (now - seconds), oldest first."""
        if self._db is None:
            raise RuntimeError("TickStore not initialized")
        cutoff = time.time() - seconds
        cursor = await self._db.execute(
            """
            SELECT market_id, asset_id, exchange, token_price, implied_share_price, implied_fdv,
                   vol24, oi, funding_rate, confidence, calibration_status, timestamp
            FROM ticks
            WHERE asset_id = ? AND timestamp > ?
            ORDER BY timestamp ASC
            """,
            (asset_id, cutoff),
        )
        cols = [c[0] for c in cursor.description]
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(zip(cols, row, strict=True)) for row in rows]

    async def cleanup_older_than(self, days: int) -> int:
        if self._db is None:
            raise RuntimeError("TickStore not initialized")
        cutoff = time.time() - days * 86_400
        cursor = await self._db.execute("DELETE FROM ticks WHERE timestamp < ?", (cutoff,))
        await self._db.commit()
        return cursor.rowcount or 0


_store: TickStore | None = None


def get_store() -> TickStore:
    global _store
    if _store is None:
        _store = TickStore()
    return _store
