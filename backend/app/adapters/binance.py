"""Binance adapter — USDⓈ-M Futures pre-IPO ("TRADIFI_PERPETUAL") instruments.

Discovery: Binance lists pre-IPO synthetics on USDⓈ-M Futures with
contractType=TRADIFI_PERPETUAL and underlyingType=PREMARKET. Probe
2026-05-21 returned one relevant contract:

  SPCXUSDT  SpaceX pre-IPO perp  (onboardDate 2026-05-21 09:25 UTC)

OPENAI / ANTHROPIC are NOT yet listed on Binance Futures as of probe.

Endpoints (public, no auth):
  GET https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=<sym>
    -> {"symbol":..., "lastPrice":"<str>", "quoteVolume":"<str>",
        "openPrice":..., "highPrice":..., "lowPrice":..., ...}
  GET https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol=<sym>
    -> {"symbol":..., "bidPrice":"<str>", "askPrice":"<str>",
        "bidQty":..., "askQty":..., "time":...}
  GET https://fapi.binance.com/fapi/v1/openInterest?symbol=<sym>
    -> {"symbol":..., "openInterest":"<str>", "time":...}
  GET https://fapi.binance.com/fapi/v1/premiumIndex?symbol=<sym>
    -> {"symbol":..., "markPrice":"<str>", "lastFundingRate":"<str>",
        "nextFundingTime":..., ...}

REST poll every poll_interval seconds for ticker + book + OI + funding.
Mirror the bingx.py shape so the supervisor handles it identically.

IMPORTANT: Binance `openInterest` is denominated in the BASE asset
(SPCX), NOT in USDT — unlike BingX where the same-named field is already
USDT. We multiply by last price to surface a comparable USD figure to
the aggregator's liquidity weighting."""

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

REST_BASE = "https://fapi.binance.com"

_ASSET_TO_BINANCE_SYMBOLS: dict[str, list[str]] = {
    "SPACEX":    ["SPCXUSDT"],
    "OPENAI":    ["OPENAIUSDT"],  # listed 2026-05-26 probe: TRADIFI_PERPETUAL
    "ANTHROPIC": [],
}

# Funding accrues every 8h on the perps. 5s poll matches BingX cadence.
POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.binance")


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Reject NaN/Inf — same guard as okx.py/bingx.py to keep non-finite
    # values out of SQLite and WS payloads.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


class BinanceAdapter(ExchangeAdapter):
    exchange_id = "binance"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def discover(self, asset_id: str) -> DiscoverResult:
        candidates = _ASSET_TO_BINANCE_SYMBOLS.get(asset_id.upper(), [])
        if not candidates:
            return DiscoverResult(
                found=False,
                error=f"no Binance symbol mapping for asset_id={asset_id}",
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
                            "priceChangePercent": ticker.get("priceChangePercent"),
                        },
                    )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")
        return DiscoverResult(found=False, error="no candidate returned a ticker")

    async def _fetch_ticker(self, client: httpx.AsyncClient, symbol: str) -> dict | None:
        r = await client.get(
            f"{REST_BASE}/fapi/v1/ticker/24hr",
            params={"symbol": symbol},
        )
        r.raise_for_status()
        body = r.json()
        # Binance returns the dict directly on success (no `code` wrapper).
        # Error shape: {"code":-1121,"msg":"Invalid symbol."} → reject.
        if not isinstance(body, dict) or "lastPrice" not in body:
            return None
        return body

    async def _fetch_book_ticker(self, client: httpx.AsyncClient, symbol: str) -> dict | None:
        try:
            r = await client.get(
                f"{REST_BASE}/fapi/v1/ticker/bookTicker",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or "bidPrice" not in body:
                return None
            return body
        except Exception:  # noqa: BLE001
            return None

    async def _fetch_open_interest(
        self, client: httpx.AsyncClient, symbol: str, last_price: float | None
    ) -> float | None:
        # Binance returns openInterest in the BASE asset (e.g. 59760.91 SPCX),
        # NOT in USDT. The aggregator's liquidity weighting expects a USD
        # figure (see okx.py and bingx.py — bingx already publishes USDT, so
        # it passes through; binance must be multiplied here). Probe
        # 2026-05-21: openInterest=59760.91 × last=$211.89 → ~$12.66M USD,
        # consistent with the $51M 24h quoteVolume.
        if last_price is None or not (last_price > 0):
            return None
        try:
            r = await client.get(
                f"{REST_BASE}/fapi/v1/openInterest",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                return None
            oi_base = _to_float(body.get("openInterest"))
            if oi_base is None:
                return None
            return oi_base * last_price
        except Exception:  # noqa: BLE001
            return None

    async def _fetch_funding_rate(self, client: httpx.AsyncClient, symbol: str) -> float | None:
        try:
            r = await client.get(
                f"{REST_BASE}/fapi/v1/premiumIndex",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                return None
            return _to_float(body.get("lastFundingRate"))
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

                    # bookTicker is a separate endpoint on Binance Futures
                    # (24hr ticker does not include bid/ask). Best-effort —
                    # tick still emits if bookTicker fails.
                    book = await self._fetch_book_ticker(client, market.symbol)
                    bid = _to_float(book.get("bidPrice")) if book else None
                    ask = _to_float(book.get("askPrice")) if book else None

                    # quoteVolume from 24hr ticker is already USDT-denominated
                    # — no multiplication needed.
                    vol24 = _to_float(ticker.get("quoteVolume"))

                    # Wall-clock timestamp (not Binance's closeTime). Same
                    # rationale as bingx.py F72: closeTime aligns to Binance's
                    # internal update cadence and can repeat across consecutive
                    # polls, colliding with our (market_id, timestamp) PK.
                    ts = time.time()

                    # OI + funding are perp-only; both best-effort. OI is
                    # converted base→USDT here (see _fetch_open_interest).
                    oi_usd = await self._fetch_open_interest(client, market.symbol, last_price)
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
                        # Binance USDⓈ-M perps settle every 8h (premiumIndex
                        # nextFundingTime - now ≈ 8h on probe).
                        funding_interval_hours=8,
                        timestamp=ts,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "binance_poll_error",
                        symbol=market.symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke test: `python -m app.adapters.binance`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = BinanceAdapter()
        for asset in ("SPACEX", "OPENAI", "ANTHROPIC"):
            result = await adapter.discover(asset)
            print(f"{asset:10s} discover: found={result.found} symbol={result.symbol} last={result.last_price}")
            if not result.found:
                print(f"           error: {result.error}")

        # Stream one SPACEX tick to sanity-check the full path
        market = Market(
            asset_id="SPACEX",
            exchange="binance",
            symbol="SPCXUSDT",
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
                f"  [{count:02d}] t={tick.timestamp:.1f} px={tick.token_price} "
                f"bid={tick.bid} ask={tick.ask} vol24={tick.vol24} "
                f"oi={tick.oi} fr={tick.funding_rate}"
            )
            if count >= 3 or time.time() - start > 20:
                break

    asyncio.run(_smoke())
