"""BingX adapter — pre-IPO ("NCSK*") perp instruments.

Discovery: BingX lists pre-IPO synthetics on Standard Futures under the
`NCSK*` symbol prefix (PreStocks + Ventuals-mirror). Probe 2026-05-20
returned three relevant contracts:

  NCSKSPACEXV2USD-USDT  "SPACEX(VNTL)-USDT"      Ventuals-mirror valuation perp
  NCSKSPACEXP2USD-USDT  "SPACEX(PreStocks)-USDT" BingX PreStocks share-price-like
  NCSKOPENAIV2USD-USDT  "OPENAI(VNTL)-USDT"      Ventuals-mirror valuation perp

ANTHROPIC is NOT yet listed on BingX (2026-05-20).

Endpoints (public, no auth):
  GET https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol=<sym>
    -> {"code":0, "data":{"symbol":..., "lastPrice":"<str>", "askPrice":...,
                          "bidPrice":..., "quoteVolume":...}}
  GET https://open-api.bingx.com/openApi/swap/v2/quote/openInterest?symbol=<sym>
    -> {"code":0, "data":{"openInterest":"<str>", "symbol":..., "time":...}}
  GET https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex?symbol=<sym>
    -> {"code":0, "data":{"lastFundingRate":"<str>", "nextFundingTime":...}}

REST poll every poll_interval seconds for ticker + OI + funding. Mirror
the bitget.py shape — same structlog naming, same backoff approach, same
asset->symbol map pattern — so the supervisor handles it identically."""

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

REST_BASE = "https://open-api.bingx.com"

# (asset_id, exchange) -> BingX symbol. The `exchange` key lets us route a
# single asset (SPACEX) to two different BingX instruments (VNTL vs
# PreStocks) without colliding — the supervisor registers per-market and
# the symbol comes from the Market row anyway. The map is consulted only
# by discover(), which is asset-id-driven; we return the first match.
_ASSET_TO_BINGX_SYMBOLS: dict[str, list[str]] = {
    "SPACEX":    ["NCSKSPACEXV2USD-USDT", "NCSKSPACEXP2USD-USDT"],
    "OPENAI":    ["NCSKOPENAIV2USD-USDT"],
    "ANTHROPIC": [],  # not yet listed on BingX as of 2026-05-20
}

# Funding accrues every 8h on the perps (premiumIndex returns
# `lastFundingRate` + `nextFundingTime`). 5s poll is plenty.
POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.bingx")


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Reject NaN/Inf — same guard as okx.py:F11 to keep non-finite values
    # out of SQLite and WS payloads.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


class BingXAdapter(ExchangeAdapter):
    exchange_id = "bingx"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def discover(self, asset_id: str) -> DiscoverResult:
        candidates = _ASSET_TO_BINGX_SYMBOLS.get(asset_id.upper(), [])
        if not candidates:
            return DiscoverResult(
                found=False,
                error=f"no BingX symbol mapping for asset_id={asset_id}",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for symbol in candidates:
                    ticker = await self._fetch_ticker(client, symbol)
                    if ticker is None:
                        continue
                    last_price = _to_float(ticker.get("lastPrice"))
                    return DiscoverResult(
                        found=True,
                        symbol=symbol,
                        last_price=last_price,
                        detail={
                            "openPrice": ticker.get("openPrice"),
                            "highPrice": ticker.get("highPrice"),
                            "lowPrice": ticker.get("lowPrice"),
                            "quoteVolume": ticker.get("quoteVolume"),
                            "bidPrice": ticker.get("bidPrice"),
                            "askPrice": ticker.get("askPrice"),
                        },
                    )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")
        return DiscoverResult(found=False, error="no candidate returned a ticker")

    async def _fetch_ticker(self, client: httpx.AsyncClient, symbol: str) -> dict | None:
        r = await client.get(
            f"{REST_BASE}/openApi/swap/v2/quote/ticker",
            params={"symbol": symbol},
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or body.get("code") != 0:
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None

    async def _fetch_open_interest(self, client: httpx.AsyncClient, symbol: str) -> float | None:
        # Returns BingX's published openInterest directly. Slice 5 F66:
        # empirical probe (NCSKSPACEXV2USD-USDT: openInterest≈980K, quote
        # volume24h≈$1.2M) confirmed the value is already USDT-denominated,
        # NOT contract count. The prior `oi * lastPrice` multiplication
        # squared the figure (yielding ~$2B OI on a $1M/day venue — physically
        # impossible for a pre-IPO synthetic perp) and tilted the consensus
        # weight toward BingX by ~1000×.
        try:
            r = await client.get(
                f"{REST_BASE}/openApi/swap/v2/quote/openInterest",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or body.get("code") != 0:
                return None
            data = body.get("data")
            if not isinstance(data, dict):
                return None
            return _to_float(data.get("openInterest"))
        except Exception:  # noqa: BLE001
            return None

    async def _fetch_funding_rate(self, client: httpx.AsyncClient, symbol: str) -> float | None:
        try:
            r = await client.get(
                f"{REST_BASE}/openApi/swap/v2/quote/premiumIndex",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or body.get("code") != 0:
                return None
            data = body.get("data")
            if not isinstance(data, dict):
                return None
            return _to_float(data.get("lastFundingRate"))
        except Exception:  # noqa: BLE001
            return None

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    ticker = await self._fetch_ticker(client, market.symbol)
                    if ticker is None:
                        raise RuntimeError("ticker fetch returned None")
                    last_price = _to_float(ticker.get("lastPrice"))
                    if last_price is None:
                        raise RuntimeError("missing lastPrice")

                    bid = _to_float(ticker.get("bidPrice"))
                    ask = _to_float(ticker.get("askPrice"))
                    # quoteVolume is already USDT-denominated 24h volume; no
                    # multiplication by price needed (unlike Bybit/some others).
                    vol24 = _to_float(ticker.get("quoteVolume"))
                    # Slice 5 F72: prefer poll-wallclock over BingX's
                    # `closeTime`. closeTime is tied to BingX's internal
                    # update cadence (sub-second but data-aligned, not poll-
                    # aligned). Several polls in a row can return the same
                    # closeTime and our `(market_id, timestamp)` PRIMARY KEY
                    # in store.py would then `INSERT OR IGNORE` them away.
                    ts = time.time()

                    # OI + funding are perp-only; both are best-effort (errors
                    # don't break the tick — the price is the primary signal).
                    # Slice 5 F66: openInterest is already USDT-denominated;
                    # no multiplication.
                    oi_usd = await self._fetch_open_interest(client, market.symbol)
                    funding_rate = await self._fetch_funding_rate(client, market.symbol)

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
                        # BingX perps settle every 8h (premiumIndex returns
                        # fundingIntervalHours=8). Slice 5 F65.
                        funding_interval_hours=8,
                        timestamp=ts,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "bingx_poll_error",
                        symbol=market.symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke test: `python -m app.adapters.bingx`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = BingXAdapter()
        for asset in ("SPACEX", "OPENAI", "ANTHROPIC"):
            result = await adapter.discover(asset)
            print(f"{asset:10s} discover: found={result.found} symbol={result.symbol} last={result.last_price}")
            if not result.found:
                print(f"           error: {result.error}")

        # Stream one SPACEX-PreStocks tick to sanity-check the full path
        market = Market(
            asset_id="SPACEX",
            exchange="bingx",
            symbol="NCSKSPACEXP2USD-USDT",
            venue="CEX",
            structure="perp",
            share_ratio=5.14,
            ratio_source="market-fitted",
            confidence=40,
        )
        start = time.time()
        count = 0
        async for tick in adapter.stream(market):
            count += 1
            print(
                f"  [{count:02d}] t={tick.timestamp:.1f} px={tick.token_price} "
                f"bid={tick.bid} ask={tick.ask} vol24={tick.vol24} oi={tick.oi} fr={tick.funding_rate}"
            )
            if count >= 3 or time.time() - start > 20:
                break

    asyncio.run(_smoke())
