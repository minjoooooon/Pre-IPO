"""WebSocket endpoint at /ws.

Protocol (server → client JSON messages):
  - snapshot: full state on connect (assets, markets, statuses, 24h history)
  - tick: a single NormalizedQuote
  - status: list of AdapterStatus when any state transitions

Client → server: ignored for v1. Reserved for future ping/subscribe filters."""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..aggregator import Aggregator

log = structlog.get_logger("app.api.ws")
router = APIRouter()


def attach(app, aggregator: Aggregator) -> None:
    """Mount the /ws endpoint with a bound Aggregator instance.

    Called from main.py lifespan so the WS handler has direct access to the
    aggregator without going through a module-level singleton."""

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        queue = await aggregator.subscribe()
        log.info("ws_client_connected")
        sender_task = asyncio.create_task(_sender(ws, queue), name="ws:sender")
        receiver_task = asyncio.create_task(_receiver(ws), name="ws:receiver")
        try:
            done, pending = await asyncio.wait(
                {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        finally:
            aggregator.unsubscribe(queue)
            log.info("ws_client_disconnected")


async def _sender(ws: WebSocket, queue: asyncio.Queue[dict]) -> None:
    try:
        while True:
            payload = await queue.get()
            await ws.send_json(payload)
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001
        log.warning("ws_sender_error", error=repr(e))


async def _receiver(ws: WebSocket) -> None:
    """Drain inbound messages so the WS doesn't back up. Currently ignored."""
    try:
        while True:
            _ = await ws.receive_text()
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001
        log.warning("ws_receiver_error", error=repr(e))
