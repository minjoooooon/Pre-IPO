"""Bitget USDT-M Futures adapter — pre-IPO RWA perp instruments.

Separate from bitget.py (which polls spot) because the futures endpoints
live under a completely different path tree (`/api/v2/mix/market/...`
vs `/api/v2/spot/market/...`) and the response shapes differ. Probe
2026-05-21 returned one relevant contract:

  SPCXUSDT  Bitget pre-IPO RWA SpaceX perp  (isRwa=YES, fundInterval=8h)

OPENAI / ANTHROPIC are NOT yet on Bitget Futures as of 2026-05-21.

Endpoints (public, no auth):
  GET https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=<sym>
    -> {"code":"00000", "data":[{"symbol":..., "sizeMultiplier":"<num>",
        "fundInterval":"8", "isRwa":"YES"/"NO", ...}]}
  GET https://api.bitget.com/api/v2/mix/market/ticker?productType=USDT-FUTURES&symbol=<sym>
    -> {"code":"00000", "data":[{"symbol":..., "lastPr":"<str>",
        "bidPr":"<str>", "askPr":"<str>", "quoteVolume":"<usdt>",
        "usdtVolume":"<usdt>", "holdingAmount":"<base>", "markPrice":...,
        "indexPrice":..., "fundingRate":"<str>", ...}]}

REST poll every poll_interval seconds. Single ticker endpoint returns
everything except sizeMultiplier (cached once at startup).

IMPORTANT Bitget Futures semantics:
- `quoteVolume`/`usdtVolume` IS USDT — passed through verbatim as vol24
  (both fields are equal on probe; using quoteVolume).
- `holdingAmount` is in the BASE asset (SPCX) — convert to USDT via
  holdingAmount × sizeMultiplier × markPrice. sizeMultiplier varies
  per contract (probe shows 0.01 for SPCXUSDT) and acts the same way
  contractSize does on MEXC/BitMart.
- `fundingRate` is the current period rate; `fundInterval` from
  /contracts gives the cycle in HOURS as a string ("8" → 8h)."""

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

_ASSET_TO_BITGET_FUTURES_SYMBOLS: dict[str, str] = {
    "SPACEX": "SPCXUSDT",
}

POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.bitget_futures")


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _to_int(x: object) -> int | None:
    if x is None or x == "":
        return None
    try:
        return int(float(x))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class BitgetFuturesAdapter(ExchangeAdapter):
    exchange_id = "bitget-futures"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None
        # Cached per-symbol: (size_multiplier, fund_interval_hours).
        # Fetched once at first poll from /contracts and reused.
        self._contract_meta: dict[str, tuple[float, int]] = {}

    async def discover(self, asset_id: str) -> DiscoverResult:
        symbol = _ASSET_TO_BITGET_FUTURES_SYMBOLS.get(asset_id.upper())
        if not symbol:
            return DiscoverResult(
                found=False,
                error=f"no Bitget Futures symbol mapping for asset_id={asset_id}",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                ticker = await self._fetch_ticker(client, symbol)
                if ticker is None:
                    return DiscoverResult(found=False, error="ticker fetch returned None")
                last_price = _to_float(ticker.get("lastPr"))
                return DiscoverResult(
                    found=True,
                    symbol=symbol,
                    last_price=last_price,
                    detail={
                        "markPrice": ticker.get("markPrice"),
                        "indexPrice": ticker.get("indexPrice"),
                        "quoteVolume": ticker.get("quoteVolume"),
                        "holdingAmount": ticker.get("holdingAmount"),
                        "fundingRate": ticker.get("fundingRate"),
                    },
                )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

    async def _fetch_ticker(self, client: httpx.AsyncClient, symbol: str) -> dict | None:
        r = await client.get(
            f"{REST_BASE}/api/v2/mix/market/ticker",
            params={"productType": "USDT-FUTURES", "symbol": symbol},
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or body.get("code") != "00000":
            return None
        data = body.get("data", [])
        if isinstance(data, list) and data:
            return data[0]
        return None

    async def _fetch_contract_meta(
        self, client: httpx.AsyncClient, symbol: str
    ) -> tuple[float | None, int | None]:
        # Fetch (sizeMultiplier, fundInterval) once. Both are static metadata
        # so caching for adapter lifetime is safe.
        try:
            r = await client.get(
                f"{REST_BASE}/api/v2/mix/market/contracts",
                params={"productType": "USDT-FUTURES", "symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or body.get("code") != "00000":
                return (None, None)
            data = body.get("data", [])
            if not (isinstance(data, list) and data):
                return (None, None)
            row = data[0]
            sz = _to_float(row.get("sizeMultiplier"))
            fih = _to_int(row.get("fundInterval"))
            return (sz, fih)
        except Exception:  # noqa: BLE001
            return (None, None)

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        symbol = market.symbol

        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    # Lazy-fetch contract meta on first iteration.
                    if symbol not in self._contract_meta:
                        sz, fih = await self._fetch_contract_meta(client, symbol)
                        if sz is not None and sz > 0 and fih is not None and fih > 0:
                            self._contract_meta[symbol] = (sz, fih)
                            log.info(
                                "bitget_futures_meta_cached",
                                symbol=symbol,
                                size_multiplier=sz,
                                fund_interval_hours=fih,
                            )

                    ticker = await self._fetch_ticker(client, symbol)
                    if ticker is None:
                        raise RuntimeError("ticker fetch returned None")
                    last_price = _to_float(ticker.get("lastPr"))
                    if last_price is None:
                        raise RuntimeError("missing lastPr")

                    bid = _to_float(ticker.get("bidPr"))
                    ask = _to_float(ticker.get("askPr"))

                    # quoteVolume IS USDT — passthrough.
                    vol24 = _to_float(ticker.get("quoteVolume"))

                    # OI: holdingAmount is BASE-asset units (SPCX); convert
                    # to USDT via × sizeMultiplier × markPrice. If meta
                    # cache missed (first poll errored), drop OI.
                    holding_amt = _to_float(ticker.get("holdingAmount"))
                    mark_price = _to_float(ticker.get("markPrice")) or last_price
                    meta = self._contract_meta.get(symbol)
                    oi_usd: float | None
                    if holding_amt is not None and meta is not None:
                        sz_mult, _ = meta
                        oi_usd = holding_amt * sz_mult * mark_price
                    else:
                        oi_usd = None

                    funding_rate = _to_float(ticker.get("fundingRate"))
                    fih = meta[1] if meta else None

                    ts = time.time()

                    self._last_tick_at = time.time()
                    backoff = settings.reconnect_initial_seconds
                    yield Tick(
                        market_id=market.market_id,
                        token_price=last_price,
                        bid=bid,
                        ask=ask,
                        vol24=vol24,
                        oi=oi_usd,
                        funding_rate=funding_rate,
                        funding_interval_hours=fih,
                        timestamp=ts,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "bitget_futures_poll_error",
                        symbol=symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke: `python -m app.adapters.bitget_futures`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = BitgetFuturesAdapter()
        for asset in ("SPACEX", "OPENAI", "ANTHROPIC"):
            result = await adapter.discover(asset)
            print(f"{asset:10s} discover: found={result.found} symbol={result.symbol} last={result.last_price}")
            if not result.found:
                print(f"           error: {result.error}")

        market = Market(
            asset_id="SPACEX",
            exchange="bitget-futures",
            symbol="SPCXUSDT",
            venue="CEX",
            structure="perp",
            share_ratio=1.0,
            ratio_source="market-fitted",
            confidence=45,
        )
        start = time.time()
        count = 0
        async for tick in adapter.stream(market):
            count += 1
            print(
                f"  [{count:02d}] t={tick.timestamp:.1f} px={tick.token_price} "
                f"bid={tick.bid} ask={tick.ask} vol24={tick.vol24} "
                f"oi={tick.oi} fr={tick.funding_rate} fih={tick.funding_interval_hours}"
            )
            if count >= 3 or time.time() - start > 20:
                break

    asyncio.run(_smoke())
