"""Aggregator: receives raw Ticks from the Supervisor, normalizes via the
registry, maintains in-memory ring buffers for chart history, persists batches
to SQLite, and broadcasts NormalizedQuote events to subscribed WS clients."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Iterable

import structlog

from .config import settings
from .models import AdapterStatus, NormalizedQuote, Tick
from .normalizer import normalize
from .registry import Registry
from .store import TickStore

log = structlog.get_logger("app.aggregator")


class Aggregator:
    def __init__(self, registry: Registry, store: TickStore) -> None:
        self._registry = registry
        self._store = store
        # ring buffer: asset_id -> exchange -> deque[NormalizedQuote] (max ring_buffer_points)
        self._ring: dict[str, dict[str, deque[NormalizedQuote]]] = {}
        self._subscribers: set[asyncio.Queue[dict | None]] = set()
        self._pending_flush: list[NormalizedQuote] = []
        self._flush_task: asyncio.Task | None = None
        self._retention_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        self._statuses_cb = None  # set by lifespan: callable returning AdapterStatus[]
        self._running = False

    # ---- lifecycle ----

    def attach_status_source(self, cb) -> None:
        self._statuses_cb = cb

    async def start(self) -> None:
        self._running = True
        # Warm ring buffers from SQLite history (last 24h)
        for asset in self._registry.list_assets():
            rows = await self._store.replay_recent(asset.id, settings.history_replay_seconds)
            for row in rows:
                quote = NormalizedQuote(
                    asset_id=row["asset_id"],
                    exchange=row["exchange"],
                    token_price=row["token_price"],
                    implied_share_price=row["implied_share_price"],
                    implied_fdv=row["implied_fdv"],
                    confidence=row["confidence"],
                    calibration_status=row["calibration_status"],
                    vol24=row["vol24"],
                    oi=row["oi"],
                    funding_rate=row["funding_rate"],
                    # funding_interval_hours isn't persisted in SQLite yet
                    # (would be a Slice 6 schema migration); warm-up uses
                    # the FE default of 8h. Live ticks carry the real
                    # cadence forward via the Tick → NormalizedQuote path.
                    funding_interval_hours=None,
                    spread_bp=None,  # not persisted; recomputed from next bid/ask
                    timestamp=row["timestamp"],
                )
                self._ring_push(quote)
        log.info(
            "aggregator_warmed",
            ring_assets=len(self._ring),
            ring_total_points=sum(
                len(d) for ex_map in self._ring.values() for d in ex_map.values()
            ),
        )

        self._flush_task = asyncio.create_task(self._flush_loop(), name="agg:flush")
        self._retention_task = asyncio.create_task(self._retention_loop(), name="agg:retention")
        self._status_task = asyncio.create_task(self._status_loop(), name="agg:status")
        log.info("aggregator_started")

    async def stop(self) -> None:
        self._running = False
        for t in (self._flush_task, self._retention_task, self._status_task):
            if t is not None:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        # final flush
        await self._flush_now()
        log.info("aggregator_stopped")

    # ---- tick sink (called by Supervisor) ----

    async def on_tick(self, tick: Tick) -> None:
        market = self._registry.get_market(tick.market_id)
        if market is None:
            log.warning("aggregator_unknown_market", market_id=tick.market_id)
            return
        asset = self._registry.get_asset(market.asset_id)
        if asset is None:
            log.warning("aggregator_unknown_asset", asset_id=market.asset_id)
            return
        quote = normalize(tick, market, asset)
        self._ring_push(quote)
        self._pending_flush.append(quote)
        # Bound the queue. If SQLite has been failing for hours, prefer to drop
        # the oldest pending entries (with a loud log) over OOMing the process.
        overflow = len(self._pending_flush) - settings.pending_flush_max
        if overflow > 0:
            del self._pending_flush[:overflow]
            log.warning(
                "aggregator_pending_overflow",
                dropped=overflow,
                pending=len(self._pending_flush),
            )
        await self._broadcast({"type": "tick", "quote": quote.model_dump(mode="json")})

    # ---- WS subscription ----

    async def subscribe(self) -> asyncio.Queue[dict | None]:
        q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1024)
        self._subscribers.add(q)
        # send initial snapshot
        await q.put(self.snapshot_message())
        return q

    def unsubscribe(self, q: asyncio.Queue[dict | None]) -> None:
        self._subscribers.discard(q)

    def snapshot_message(self) -> dict:
        history: dict[str, dict[str, list[dict]]] = {}
        for asset_id, ex_map in self._ring.items():
            history[asset_id] = {
                exchange: [q.model_dump(mode="json") for q in dq] for exchange, dq in ex_map.items()
            }
        statuses: list[dict] = []
        if self._statuses_cb is not None:
            try:
                statuses = [s.model_dump(mode="json") if isinstance(s, AdapterStatus) else s for s in self._statuses_cb()]
            except Exception as e:  # noqa: BLE001
                log.exception("snapshot_status_error", error=repr(e))
        return {
            "type": "snapshot",
            "assets": [a.model_dump(mode="json") for a in self._registry.list_assets()],
            "markets": [m.model_dump(mode="json") for m in self._registry.list_markets()],
            "statuses": statuses,
            "history": history,
        }

    async def broadcast_status(self, statuses: Iterable[AdapterStatus]) -> None:
        payload = {"type": "status", "statuses": [s.model_dump(mode="json") for s in statuses]}
        await self._broadcast(payload)

    async def broadcast_config_update(self) -> None:
        """Push refreshed assets/markets metadata after an admin write so live
        FE clients re-render with the new share_ratio / fully_diluted_shares
        without needing a full snapshot/reload."""
        payload = {
            "type": "config-update",
            "assets": [a.model_dump(mode="json") for a in self._registry.list_assets()],
            "markets": [m.model_dump(mode="json") for m in self._registry.list_markets()],
        }
        await self._broadcast(payload)

    # ---- private ----

    def _ring_push(self, quote: NormalizedQuote) -> None:
        ex_map = self._ring.setdefault(quote.asset_id, {})
        dq = ex_map.setdefault(quote.exchange, deque(maxlen=settings.ring_buffer_points))
        dq.append(quote)

    async def _broadcast(self, payload: dict) -> None:
        if not self._subscribers:
            return
        dead: list[asyncio.Queue[dict | None]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # subscriber is too slow; mark for removal
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)
            # Wake the (parked) _sender so it sees the close signal instead of
            # sitting on get() forever on an orphaned queue. The WS would
            # otherwise stay "open" with zero data flowing.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)
            log.warning("dropped_slow_subscriber")

    async def _flush_loop(self) -> None:
        # First flush happens sooner so warm-up data survives a same-minute crash.
        await asyncio.sleep(settings.tick_batch_first_flush_seconds)
        await self._flush_now()
        while self._running:
            await asyncio.sleep(settings.tick_batch_flush_seconds)
            await self._flush_now()

    async def _status_loop(self) -> None:
        """Periodically broadcast adapter statuses to all WS subscribers.

        Slice 5 F68: `broadcast_status()` existed since slice 1 but was
        never called — the FE only saw status updates on initial snapshot
        and on config-update broadcasts. With long-lived dashboards this
        meant a stale/disconnected adapter never surfaced in the FE
        FeedsCell or DISABLED chips until the operator refreshed.

        Cadence matches the supervisor's `_stale_loop` (30s) — anything
        finer would just spam clients without new information."""
        while self._running:
            await asyncio.sleep(settings.status_broadcast_interval_seconds)
            if self._statuses_cb is None:
                continue
            try:
                statuses = self._statuses_cb()
                await self.broadcast_status(statuses)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("aggregator_status_broadcast_failed", error=repr(e))

    async def _retention_loop(self) -> None:
        """Periodically delete ticks older than history_retention_days.
        Without this, ticks.db grows without bound on a 24/7 process."""
        while self._running:
            await asyncio.sleep(settings.retention_cleanup_interval_seconds)
            try:
                deleted = await self._store.cleanup_older_than(settings.history_retention_days)
                if deleted > 0:
                    log.info("aggregator_retention_cleanup", deleted=deleted)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("aggregator_retention_failed", error=repr(e))

    async def _flush_now(self) -> None:
        if not self._pending_flush:
            return
        # Take a reference, but do NOT detach yet — we want to keep the batch
        # in _pending_flush if the await is cancelled or fails, so the next
        # iteration retries. Only clear after a confirmed successful insert.
        batch = list(self._pending_flush)
        try:
            n = await self._store.insert_batch(batch)
            # Successful commit. Remove exactly the items we just persisted;
            # new ticks may have arrived concurrently and must remain queued.
            self._pending_flush = self._pending_flush[len(batch):]
            log.debug("aggregator_flushed", count=n)
        except asyncio.CancelledError:
            # Don't clear — next start-up / next loop iteration will retry.
            log.warning("aggregator_flush_cancelled", pending=len(self._pending_flush))
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("aggregator_flush_failed", error=repr(e), pending=len(self._pending_flush))
