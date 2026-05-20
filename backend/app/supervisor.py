"""Adapter task supervisor.

Spawns one asyncio task per (adapter, market). If a task crashes, logs the
error and restarts it after exponential backoff. One bad parser MUST NOT kill
the aggregator or sibling adapters."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

from .adapters.base import ExchangeAdapter
from .config import settings
from .models import AdapterStatus, Market, Tick

log = structlog.get_logger("app.supervisor")

TickSink = Callable[[Tick], Awaitable[None]]


@dataclass
class _SupervisedAdapter:
    adapter: ExchangeAdapter
    market: Market
    task: asyncio.Task | None = None
    # Wall-clock time when the supervisor last (re)entered the "connected"
    # state for this adapter. Used to detect never-ticked stale: an adapter
    # that subscribes but never receives a tick should still go stale once
    # `connected_at` ages past `stale_threshold_seconds`.
    connected_at: float | None = None
    status: AdapterStatus = field(init=False)

    def __post_init__(self) -> None:
        self.status = AdapterStatus(
            market_id=self.market.market_id,
            exchange=self.market.exchange,
            state="disconnected",
        )


class Supervisor:
    def __init__(self, sink: TickSink) -> None:
        self._sink = sink
        self._adapters: list[_SupervisedAdapter] = []
        self._stale_task: asyncio.Task | None = None
        self._running = False

    def register(self, adapter: ExchangeAdapter, market: Market) -> None:
        self._adapters.append(_SupervisedAdapter(adapter=adapter, market=market))

    def disable(self, market_id: str, reason: str) -> None:
        """Mark a single supervised adapter (by market_id) disabled.

        Matching on `market.market_id` rather than `adapter.exchange_id`
        prevents an uncalibrated row from blackholing sibling markets that
        share the same exchange (e.g. one uncalibrated OKX market would
        otherwise disable all OKX feeds). Also covers the inverse hyperliquid
        case where `exchange_id` ("hyperliquid") doesn't match the per-DEX
        market exchange ("hl-xyz"/"hl-vntl") — a market_id match is exact."""
        for sa in self._adapters:
            if sa.market.market_id == market_id:
                sa.status = AdapterStatus(
                    market_id=sa.market.market_id,
                    exchange=sa.market.exchange,
                    state="disabled",
                    last_error=reason,
                )
                return
        log.warning("supervisor_disable_no_match", market_id=market_id, reason=reason)

    def statuses(self) -> list[AdapterStatus]:
        return [sa.status for sa in self._adapters]

    async def start(self) -> None:
        self._running = True
        for sa in self._adapters:
            if sa.status.state == "disabled":
                log.info("supervisor_skip_disabled", exchange=sa.adapter.exchange_id)
                continue
            sa.task = asyncio.create_task(
                self._run_with_restart(sa),
                name=f"adapter:{sa.adapter.exchange_id}",
            )
        self._stale_task = asyncio.create_task(self._stale_loop(), name="supervisor:stale")
        log.info("supervisor_started", adapters=len(self._adapters))

    async def stop(self) -> None:
        self._running = False
        tasks = [sa.task for sa in self._adapters if sa.task is not None]
        if self._stale_task is not None:
            tasks.append(self._stale_task)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, BaseException):
                await t
        log.info("supervisor_stopped")

    async def _run_with_restart(self, sa: _SupervisedAdapter) -> None:
        backoff = settings.reconnect_initial_seconds
        while self._running:
            sa.connected_at = time.time()
            # Preserve last_error across status resets so /health and the FE
            # badge keep showing the most recent crash reason until the next
            # one supersedes it.
            sa.status = AdapterStatus(
                market_id=sa.market.market_id,
                exchange=sa.market.exchange,
                state="connected",
                last_tick_at=sa.status.last_tick_at,
                reconnect_count=sa.status.reconnect_count,
                last_error=sa.status.last_error,
            )
            clean_exit = False
            try:
                async for tick in sa.adapter.stream(sa.market):
                    sa.status.last_tick_at = time.time()
                    if sa.status.state != "connected":
                        sa.status.state = "connected"
                    # A healthy tick means the connection works — reset the
                    # restart backoff so the next genuine disconnect starts
                    # from `reconnect_initial_seconds` again instead of staying
                    # pinned at `reconnect_max_seconds` forever.
                    backoff = settings.reconnect_initial_seconds
                    try:
                        await self._sink(tick)
                    except Exception as e:  # noqa: BLE001
                        # Sink failures must not kill the stream
                        log.exception("supervisor_sink_error", error=repr(e))
                # generator returned normally — adapter chose to end its
                # stream. Treat as a soft restart with backoff (not as a crash).
                if not self._running:
                    return
                clean_exit = True
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                sa.status.state = "reconnecting"
                sa.status.reconnect_count += 1
                sa.status.last_error = repr(e)
                log.warning(
                    "supervisor_adapter_crash",
                    exchange=sa.adapter.exchange_id,
                    backoff=backoff,
                    reconnect_count=sa.status.reconnect_count,
                    error=repr(e),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, settings.reconnect_max_seconds)
                continue

            if clean_exit:
                sa.status.state = "reconnecting"
                log.info(
                    "supervisor_adapter_clean_exit",
                    exchange=sa.adapter.exchange_id,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, settings.reconnect_max_seconds)

    async def _stale_loop(self) -> None:
        """Every 30s, mark any adapter with no tick in stale_threshold_seconds
        as 'stale'. The aggregator can use this to update broadcasts.

        A never-ticked adapter (subscribed but no first tick) is also marked
        stale once `connected_at` ages past the threshold — otherwise a
        wedged subscription with no data would sit `connected` forever."""
        while self._running:
            await asyncio.sleep(30)
            now = time.time()
            for sa in self._adapters:
                if sa.status.state in ("disabled", "reconnecting", "disconnected"):
                    continue
                # Reference point: last tick if we ever got one, otherwise the
                # time we (re)entered the "connected" state.
                reference = sa.status.last_tick_at
                if reference is None:
                    reference = sa.connected_at
                if reference is None:
                    continue  # task not yet started
                age = now - reference
                if age > settings.stale_threshold_seconds:
                    if sa.status.state != "stale":
                        sa.status.state = "stale"
                        log.warning(
                            "adapter_stale",
                            exchange=sa.adapter.exchange_id,
                            last_tick_age=age,
                            never_ticked=sa.status.last_tick_at is None,
                        )
