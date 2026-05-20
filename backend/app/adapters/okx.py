"""OKX adapter.

P1.5 finding (2026-05-18 probe):
  GET /api/v5/public/instruments?instType=SWAP returns SPACEX-USDT-SWAP
  with: ctVal=1, ctValCcy=SPACEX, ctMult=1, ctType=linear, lotSz=0.01
  Live ticker /api/v5/market/ticker?instId=SPACEX-USDT-SWAP returns
  last=2332.1, bid/ask=2328/2328.1, vol24h=15849 contracts.

WS public endpoint (v5):
  wss://ws.okx.com:8443/ws/v5/public
  - Subscribe via {"op":"subscribe","args":[{"channel":"<c>","instId":"<id>"}]}
  - Ping/pong: client sends literal string "ping", server replies "pong"
    (NOT JSON). 25s server-side timeout; we ping every 20s.
  - All numeric fields are STRINGS - explicit float() required.
  - Rate limit: 480 sub/unsub/login per connection per hour. We multiplex
    all channels for one symbol on one connection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import structlog
import websockets

from ..config import settings
from ..models import Market, Tick
from .base import DiscoverResult, ExchangeAdapter

REST_BASE = "https://www.okx.com"
WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"

# asset_id → expected OKX instId. Each is verified via /public/instruments
# at startup (discover()). All currently use ctVal=1, ctValCcy=<base>, so
# vol24/oi USD conversion = contracts × token_price.
_ASSET_TO_OKX_INSTID: dict[str, str] = {
    "SPACEX": "SPACEX-USDT-SWAP",
    "OPENAI": "OPENAI-USDT-SWAP",
    "ANTHROPIC": "ANTHROPIC-USDT-SWAP",
}

log = structlog.get_logger("app.adapters.okx")


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Reject NaN and +/-Inf — these propagate silently through normalization,
    # SQLite, and JSON and break the FE math.
    if not math.isfinite(v):
        return None
    return v


@dataclass
class _ChannelState:
    """Latest values aggregated across the three channels per market."""

    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    vol24: float | None = None
    funding_rate: float | None = None
    oi: float | None = None
    last_ticker_ts: float | None = None  # exchange-reported ms epoch


@dataclass
class _Stats:
    reconnect_count: int = 0
    last_tick_at: float | None = None
    last_error: str | None = None
    parse_errors: int = 0
    messages_received: int = 0
    extra: dict[str, object] = field(default_factory=dict)


class OkxAdapter(ExchangeAdapter):
    exchange_id = "okx"

    def __init__(self) -> None:
        self._stats = _Stats()

    # ---------- discover ----------

    async def discover(self, asset_id: str) -> DiscoverResult:
        inst_id = _ASSET_TO_OKX_INSTID.get(asset_id.upper())
        if inst_id is None:
            return DiscoverResult(
                found=False,
                error=f"no OKX instId mapping for asset_id={asset_id}",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                inst_r = await client.get(
                    f"{REST_BASE}/api/v5/public/instruments",
                    params={"instType": "SWAP"},
                )
                inst_r.raise_for_status()
                inst_body = inst_r.json()

                tick_r = await client.get(
                    f"{REST_BASE}/api/v5/market/ticker",
                    params={"instId": inst_id},
                )
                tick_r.raise_for_status()
                tick_body = tick_r.json()
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

        instruments = inst_body.get("data", []) if isinstance(inst_body, dict) else []
        match = next(
            (i for i in instruments if isinstance(i, dict) and i.get("instId") == inst_id),
            None,
        )
        if match is None:
            return DiscoverResult(
                found=False,
                symbol=inst_id,
                error=f"instId={inst_id} not in /public/instruments (SWAP)",
            )

        last_price: float | None = None
        ticker_data = tick_body.get("data", []) if isinstance(tick_body, dict) else []
        if ticker_data and isinstance(ticker_data[0], dict):
            last_price = _to_float(ticker_data[0].get("last"))

        return DiscoverResult(
            found=True,
            symbol=inst_id,
            last_price=last_price,
            detail={
                "ct_val": match.get("ctVal"),
                "ct_val_ccy": match.get("ctValCcy"),
                "ct_mult": match.get("ctMult"),
                "ct_type": match.get("ctType"),
                "lot_sz": match.get("lotSz"),
                "state": match.get("state"),
            },
        )

    # ---------- stats accessor ----------

    @property
    def stats(self) -> _Stats:
        return self._stats

    # ---------- streaming ----------

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        """Long-running tick stream. Reconnects with exponential backoff on
        connection failures. Each parse failure is logged and counted, but does
        not kill the stream (per-message try/except)."""

        inst_id = market.symbol
        backoff = settings.reconnect_initial_seconds
        max_backoff = settings.reconnect_max_seconds

        while True:
            # Fresh per-connection state so a delisting/relisting during the
            # disconnect doesn't carry old bid/ask/oi across the boundary.
            state = _ChannelState()
            try:
                async for tick in self._connect_once(inst_id, market, state):
                    backoff = settings.reconnect_initial_seconds  # reset on success
                    yield tick
                # Clean close (server-initiated graceful close, scheduled
                # maintenance, etc.). Treat as a soft restart — without this
                # backoff the outer `while True` would spin and rate-limit us.
                log.info("okx_clean_close", inst_id=inst_id, backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._stats.reconnect_count += 1
                self._stats.last_error = repr(e)
                log.warning(
                    "okx_reconnect",
                    inst_id=inst_id,
                    backoff=backoff,
                    reconnect_count=self._stats.reconnect_count,
                    error=repr(e),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _connect_once(
        self, inst_id: str, market: Market, state: _ChannelState
    ) -> AsyncIterator[Tick]:
        log.info("okx_connecting", inst_id=inst_id)
        async with websockets.connect(
            WS_PUBLIC,
            ping_interval=None,  # we do our own "ping"/"pong" string heartbeat
            close_timeout=5,
            max_size=2**20,
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": [
                            {"channel": "tickers", "instId": inst_id},
                            {"channel": "funding-rate", "instId": inst_id},
                            {"channel": "open-interest", "instId": inst_id},
                        ],
                    }
                )
            )
            log.info("okx_subscribed", inst_id=inst_id)

            heartbeat = asyncio.create_task(self._heartbeat(ws))
            try:
                async for raw in ws:
                    try:
                        tick = self._handle_message(raw, market, state)
                    except Exception as e:  # noqa: BLE001 — surface, don't crash
                        self._stats.parse_errors += 1
                        log.warning("okx_parse_error", error=repr(e), raw=str(raw)[:200])
                        continue
                    if tick is not None:
                        self._stats.last_tick_at = time.time()
                        yield tick
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    async def _heartbeat(self, ws: websockets.ClientConnection) -> None:
        try:
            while True:
                await asyncio.sleep(settings.okx_heartbeat_interval_seconds)
                try:
                    await ws.send("ping")
                except Exception as e:  # noqa: BLE001
                    # A failed ping means the socket is dead. Close it so the
                    # outer `async for raw in ws` raises and reconnect kicks in
                    # — without this the reader can wait on a dead socket
                    # indefinitely.
                    log.warning("okx_heartbeat_send_failed", error=repr(e))
                    with contextlib.suppress(Exception):
                        await ws.close()
                    return
        except asyncio.CancelledError:
            raise

    def _handle_message(
        self, raw: str | bytes, market: Market, state: _ChannelState
    ) -> Tick | None:
        # Server may reply "pong" to our "ping" — plain string, not JSON.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if raw == "pong":
            return None

        msg = json.loads(raw)
        self._stats.messages_received += 1

        # Subscribe ack messages: {"event":"subscribe","arg":{...}}
        if "event" in msg:
            event = msg.get("event")
            if event == "error":
                self._stats.last_error = msg.get("msg", "subscribe error")
                log.warning("okx_event_error", payload=msg)
                # Subscribe rejection (bad instId, banned IP, auth fail) leaves
                # the WS open with no data. Raise so the outer reconnect path
                # closes and retries instead of silently idling until stale
                # detection fires 60s later.
                raise RuntimeError(f"OKX event error: {msg.get('msg')!r} code={msg.get('code')}")
            return None

        arg = msg.get("arg") or {}
        channel = arg.get("channel")
        data = msg.get("data") or []
        if not data:
            return None
        row = data[0] if isinstance(data, list) else data
        if not isinstance(row, dict):
            return None

        # Update channel state. For SPACEX-USDT-SWAP: ctVal=1 SPACEX/contract,
        # quote currency = USDT. So USD value = contracts * ctVal * mark_price.
        # We store raw contract counts in state, convert to USD at emit time so
        # the latest price is used.
        emit_tick = False
        if channel == "tickers":
            # Only overwrite fields when the parsed value is a number. OKX may
            # emit a tickers frame with `last` present but `bidPx`/`askPx`
            # omitted (illiquid book); blanket-assigning None would erase a
            # valid quote and make spread_bp flicker between defined and
            # undefined on every other tick. Explicit `is not None` also
            # guards against the `or`-falsy bug where a legitimate 0.0 (halt,
            # delisting) gets masked by the previous price.
            v = _to_float(row.get("last"))
            if v is not None:
                state.last_price = v
            v = _to_float(row.get("bidPx"))
            if v is not None:
                state.bid = v
            v = _to_float(row.get("askPx"))
            if v is not None:
                state.ask = v
            # vol24h is in contracts for SWAP; volCcy24h is also contracts here
            # (ctValCcy == base currency). Multiply by price below to get USD.
            v = _to_float(row.get("vol24h"))
            if v is not None:
                state.vol24 = v
            ts_ms = _to_float(row.get("ts"))
            state.last_ticker_ts = ts_ms / 1000.0 if ts_ms else None
            emit_tick = True
        elif channel == "funding-rate":
            state.funding_rate = _to_float(row.get("fundingRate"))
        elif channel == "open-interest":
            # `oiCcy` is OI in base currency (= SPACEX units here); `oi` is in
            # contracts. With ctVal=1 they're equivalent. Multiply by price for USD.
            oi_ccy = _to_float(row.get("oiCcy"))
            oi_contracts = _to_float(row.get("oi"))
            state.oi = oi_ccy if oi_ccy is not None else oi_contracts
        else:
            return None

        if not emit_tick or state.last_price is None:
            return None

        # Convert contract-denominated vol24/oi to USD using current price.
        # ctVal=1 for SPACEX-USDT-SWAP, so multiply directly.
        vol24_usd = state.vol24 * state.last_price if state.vol24 is not None else None
        oi_usd = state.oi * state.last_price if state.oi is not None else None

        return Tick(
            market_id=market.market_id,
            token_price=state.last_price,
            bid=state.bid,
            ask=state.ask,
            vol24=vol24_usd,
            oi=oi_usd,
            funding_rate=state.funding_rate,
            # OKX perps settle funding every 8h (per OKX docs). Slice 5 F65:
            # tag the cadence so the FE doesn't default-annualize wrongly.
            funding_interval_hours=8,
            timestamp=state.last_ticker_ts or time.time(),
        )


# ---------------------------------------------------------------------------
# Standalone smoke test: `python -m app.adapters.okx`
# Streams SPACEX-USDT-SWAP for ~60s and prints ticks. Used in P2 verification.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import io
    import sys

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = OkxAdapter()
        result = await adapter.discover("SPACEX")
        print(f"discover: found={result.found} symbol={result.symbol} last={result.last_price}")
        if not result.found:
            print(f"  error: {result.error}")
            return

        market = Market(
            asset_id="SPACEX",
            exchange="okx",
            symbol=result.symbol or "SPACEX-USDT-SWAP",
            venue="CEX",
            structure="perp",
            share_ratio=1.0,
            ratio_source="market-fitted",
            confidence=40,
        )

        start = time.time()
        count = 0
        async for tick in adapter.stream(market):
            count += 1
            print(
                f"[{count:04d}] t={tick.timestamp:.1f} "
                f"px={tick.token_price} bid={tick.bid} ask={tick.ask} "
                f"vol={tick.vol24} oi={tick.oi} fr={tick.funding_rate}"
            )
            if time.time() - start > 60:
                break
        print(
            f"\nstats: messages={adapter.stats.messages_received} "
            f"parse_errors={adapter.stats.parse_errors} "
            f"reconnects={adapter.stats.reconnect_count}"
        )

    asyncio.run(_smoke())
