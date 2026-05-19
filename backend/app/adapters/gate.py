"""Gate adapter.

P-Gate finding (2026-05-20 probe):
  SPCX is on Gate spot as SPCX_USDT (Pre-IPO Mirror Note / Contingent Payout Note).
  Endpoint: GET https://api.gateio.ws/api/v4/spot/tickers?currency_pair=SPCX_USDT
  Response shape (array, even for a single pair):
    [{"currency_pair":"SPCX_USDT","last":"789.23",
      "lowest_ask":"790.54","lowest_size":"0.019",
      "highest_bid":"789.23","highest_size":"1.968",
      "change_percentage":"-3.14",
      "base_volume":"872.258","quote_volume":"697735.83832",
      "high_24h":"819","low_24h":"782"}]

  Gate v4 does NOT include a timestamp in the tickers payload, so we stamp
  with time.time() at receive. All numeric fields are strings — explicit float().

  Spot — no funding rate, no open interest.

  Public WS is wss://api.gateio.ws/ws/v4/ with channel spot.tickers (sub-second
  push). REST polling is sufficient for a low-frequency mirror note; switch to
  WS only if polling load or latency becomes an issue."""

from __future__ import annotations

import asyncio
import io
import sys
import time
from collections.abc import AsyncIterator

import httpx
import structlog

from ..config import settings
from ..models import Market, Tick
from .base import DiscoverResult, ExchangeAdapter

REST_BASE = "https://api.gateio.ws"

_ASSET_TO_GATE_SYMBOL: dict[str, str] = {
    "SPACEX": "SPCX_USDT",
}

POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.gate")


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class GateAdapter(ExchangeAdapter):
    exchange_id = "gate"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def discover(self, asset_id: str) -> DiscoverResult:
        symbol = _ASSET_TO_GATE_SYMBOL.get(asset_id.upper())
        if symbol is None:
            return DiscoverResult(
                found=False,
                error=f"no Gate symbol mapping for asset_id={asset_id}",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{REST_BASE}/api/v4/spot/tickers",
                    params={"currency_pair": symbol},
                )
                r.raise_for_status()
                body = r.json()
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

        if not isinstance(body, list) or not body:
            return DiscoverResult(
                found=False,
                symbol=symbol,
                error=f"unexpected response shape: {type(body).__name__}",
            )

        row = body[0]
        if not isinstance(row, dict):
            return DiscoverResult(found=False, symbol=symbol, error="row not a dict")

        last_price = _to_float(row.get("last"))
        return DiscoverResult(
            found=True,
            symbol=symbol,
            last_price=last_price,
            detail={
                "highest_bid": row.get("highest_bid"),
                "lowest_ask": row.get("lowest_ask"),
                "high_24h": row.get("high_24h"),
                "low_24h": row.get("low_24h"),
                "base_volume": row.get("base_volume"),
                "quote_volume": row.get("quote_volume"),
                "change_percentage": row.get("change_percentage"),
            },
        )

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    r = await client.get(
                        f"{REST_BASE}/api/v4/spot/tickers",
                        params={"currency_pair": market.symbol},
                    )
                    r.raise_for_status()
                    body = r.json()
                    if not isinstance(body, list) or not body:
                        raise RuntimeError(f"unexpected body shape: {type(body).__name__}")
                    row = body[0]
                    if not isinstance(row, dict):
                        raise RuntimeError("row not a dict")

                    last_price = _to_float(row.get("last"))
                    if last_price is None:
                        raise RuntimeError("missing last")

                    bid = _to_float(row.get("highest_bid"))
                    ask = _to_float(row.get("lowest_ask"))
                    # quote_volume is USD-denominated 24h volume (more useful than base_volume)
                    vol24 = _to_float(row.get("quote_volume"))
                    # Gate v4 tickers payload does not include a server timestamp —
                    # stamp at receive. Aggregator's stale detection will work off
                    # this just fine since polling cadence is fixed.
                    ts = time.time()

                    self._last_tick_at = ts
                    backoff = settings.reconnect_initial_seconds
                    yield Tick(
                        market_id=market.market_id,
                        token_price=last_price,
                        bid=bid,
                        ask=ask,
                        vol24=vol24,
                        oi=None,           # spot — no open interest
                        funding_rate=None,  # spot — no funding
                        timestamp=ts,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "gate_poll_error",
                        symbol=market.symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke test: `python -m app.adapters.gate`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = GateAdapter()
        result = await adapter.discover("SPACEX")
        print(f"discover: found={result.found} symbol={result.symbol} last={result.last_price}")
        if not result.found:
            print(f"  error: {result.error}")
            return

        market = Market(
            asset_id="SPACEX",
            exchange="gate",
            symbol=result.symbol or "SPCX_USDT",
            venue="CEX",
            structure="pre-ipo-certificate",
            share_ratio=5.01,
            ratio_source="back-solved",
            confidence=65,
        )

        start = time.time()
        count = 0
        async for tick in adapter.stream(market):
            count += 1
            print(
                f"[{count:03d}] t={tick.timestamp:.1f} "
                f"px={tick.token_price} bid={tick.bid} ask={tick.ask} vol24={tick.vol24}"
            )
            if time.time() - start > 30:
                break

    asyncio.run(_smoke())
