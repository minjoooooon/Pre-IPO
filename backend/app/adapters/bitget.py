"""Bitget adapter.

P1.5 finding (2026-05-18 probe):
  preSPAX is NOT in mix-futures. It IS on SPOT as PRESPAXUSDT.
  Endpoint: GET https://api.bitget.com/api/v2/spot/market/tickers?symbol=PRESPAXUSDT
  Response shape:
    {"code":"00000", "msg":"success",
     "data":[{"symbol":"PRESPAXUSDT", "lastPr":"843", "open":"796.81",
              "high24h":"850", "low24h":"778", "baseVolume":"293...",
              "quoteVolume":"2403648.05", ...}]}

P5 implementation: REST polling every poll_interval seconds. Spot market has
no funding rate, no open interest (would require a different endpoint). All
numeric fields are strings — explicit float()."""

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

REST_BASE = "https://api.bitget.com"

_ASSET_TO_BITGET_SYMBOL: dict[str, str] = {
    "SPACEX": "PRESPAXUSDT",
    "OPENAI": "PREOPAIUSDT",
}

# Mirror-credit on spot moves slowly; 5s polling is plenty.
POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.bitget")


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class BitgetAdapter(ExchangeAdapter):
    exchange_id = "bitget"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def discover(self, asset_id: str) -> DiscoverResult:
        symbol = _ASSET_TO_BITGET_SYMBOL.get(asset_id.upper())
        if symbol is None:
            return DiscoverResult(
                found=False,
                error=f"no Bitget symbol mapping for asset_id={asset_id}",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{REST_BASE}/api/v2/spot/market/tickers",
                    params={"symbol": symbol},
                )
                r.raise_for_status()
                body = r.json()
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

        if not isinstance(body, dict) or body.get("code") != "00000":
            return DiscoverResult(
                found=False,
                symbol=symbol,
                error=f"unexpected response code={body.get('code') if isinstance(body, dict) else 'n/a'}",
            )

        rows = body.get("data") or []
        if not rows:
            return DiscoverResult(found=False, symbol=symbol, error="empty data array")

        row = rows[0]
        last_price = _to_float(row.get("lastPr"))
        return DiscoverResult(
            found=True,
            symbol=symbol,
            last_price=last_price,
            detail={
                "open": row.get("open"),
                "high24h": row.get("high24h"),
                "low24h": row.get("low24h"),
                "baseVolume": row.get("baseVolume"),
                "quoteVolume": row.get("quoteVolume"),
            },
        )

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    r = await client.get(
                        f"{REST_BASE}/api/v2/spot/market/tickers",
                        params={"symbol": market.symbol},
                    )
                    r.raise_for_status()
                    body = r.json()
                    if not isinstance(body, dict) or body.get("code") != "00000":
                        raise RuntimeError(
                            f"unexpected body code={body.get('code') if isinstance(body, dict) else 'n/a'}"
                        )
                    rows = body.get("data") or []
                    if not rows:
                        raise RuntimeError("empty data array")
                    row = rows[0]
                    last_price = _to_float(row.get("lastPr"))
                    if last_price is None:
                        raise RuntimeError("missing lastPr")

                    bid = _to_float(row.get("bidPr"))
                    ask = _to_float(row.get("askPr"))
                    vol24 = _to_float(row.get("quoteVolume"))  # USD volume more useful than base
                    ts_ms = _to_float(row.get("ts"))
                    ts = ts_ms / 1000.0 if ts_ms else time.time()

                    self._last_tick_at = time.time()
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
                        "bitget_poll_error",
                        symbol=market.symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke test: `python -m app.adapters.bitget`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = BitgetAdapter()
        result = await adapter.discover("SPACEX")
        print(f"discover: found={result.found} symbol={result.symbol} last={result.last_price}")
        if not result.found:
            print(f"  error: {result.error}")
            return

        market = Market(
            asset_id="SPACEX",
            exchange="bitget",
            symbol=result.symbol or "PRESPAXUSDT",
            venue="CEX",
            structure="mirror-credit",
            share_ratio=1.0,
            ratio_source="official",
            confidence=70,
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
