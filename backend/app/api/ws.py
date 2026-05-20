"""WebSocket endpoint at /ws.

Protocol (server → client JSON messages):
  - snapshot: full state on connect (assets, markets, statuses, 24h history)
  - tick: a single NormalizedQuote
  - status: list of AdapterStatus when any state transitions
  - None (sentinel, internal): aggregator dropped this subscriber → sender closes WS

Client → server: ignored for v1. Reserved for future ping/subscribe filters."""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..aggregator import Aggregator
from ..config import settings

log = structlog.get_logger("app.api.ws")
router = APIRouter()


def _origin_allowed(origin: str | None) -> bool:
    # WebSocket upgrades bypass CORS middleware entirely; enforce origin here.
    # Allow no-origin (non-browser clients like curl/wscat) only when the
    # operator's allow-list opts in by listing "null" (matches file:// too).
    if origin is None:
        return "null" in settings.cors_allow_origins
    return origin in settings.cors_allow_origins


def attach(app, aggregator: Aggregator) -> None:
    """Mount the /ws endpoint with a bound Aggregator instance.

    Called from main.py lifespan so the WS handler has direct access to the
    aggregator without going through a module-level singleton."""

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if not _origin_allowed(origin):
            log.warning("ws_origin_rejected", origin=origin)
            await ws.close(code=1008)  # policy violation
            return
        await ws.accept()
        # Initialize before the try so the finally cleanup never references
        # an unbound name (subscribe() can raise — e.g. snapshot_message
        # validation error — which would have masked the original exception
        # with UnboundLocalError and left the client's WS half-open).
        queue: asyncio.Queue[dict | None] | None = None
        try:
            queue = await aggregator.subscribe()
        except Exception as e:  # noqa: BLE001
            log.exception("ws_subscribe_failed", error=repr(e))
            with contextlib.suppress(Exception):
                await ws.close(code=1011)  # internal error
            return
        log.info("ws_client_connected")
        sender_task = asyncio.create_task(_sender(ws, queue), name="ws:sender")
        receiver_task = asyncio.create_task(_receiver(ws), name="ws:receiver")
        try:
            _done, pending = await asyncio.wait(
                {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        finally:
            if queue is not None:
                aggregator.unsubscribe(queue)
            log.info("ws_client_disconnected")


async def _sender(ws: WebSocket, queue: asyncio.Queue[dict | None]) -> None:
    try:
        while True:
            payload = await queue.get()
            # Sentinel: aggregator dropped this subscriber (slow). Close so the
            # main task wakes and the WS doesn't sit as a zombie with the
            # client getting zero data but the socket "open".
            if payload is None:
                log.warning("ws_sender_dropped_by_aggregator")
                with contextlib.suppress(Exception):
                    await ws.close(code=1013)  # try again later
                return
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
