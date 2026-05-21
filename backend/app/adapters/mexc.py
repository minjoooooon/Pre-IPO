"""MEXC adapter — Contract (Futures) pre-IPO instruments.

MEXC lists pre-IPO synthetics on its Contract (Futures) platform at
`contract.mexc.com`. Probe 2026-05-21 returned four relevant contracts:

  SPACEX_USDT      1B-basis valuation perp ($2406, OKX/hl-vntl family)
  SPCXSTOCK_USDT   stock-style perp ($212, hl-xyz/Binance family)
  OPENAI_USDT      1B-basis valuation perp ($1476, premium vs hl-vntl $1180)
  ANTHROPIC_USDT   1B-basis valuation perp ($1657, premium vs hl-vntl $1196)

Endpoints (public, no auth):
  GET https://contract.mexc.com/api/v1/contract/detail?symbol=<sym>
    -> {"success":true, "data":{"symbol":..., "contractSize":<num>,
        "baseCoin":..., "quoteCoin":..., ...}}
  GET https://contract.mexc.com/api/v1/contract/ticker?symbol=<sym>
    -> {"success":true, "data":{"lastPrice":..., "bid1":..., "ask1":...,
        "volume24":<contracts>, "amount24":<usdt>, "holdVol":<contracts>,
        "indexPrice":..., "fairPrice":..., "fundingRate":...}}
  GET https://contract.mexc.com/api/v1/contract/funding_rate/<sym>
    -> {"success":true, "data":{"fundingRate":..., "collectCycle":8,
        "nextSettleTime":..., ...}}

REST poll every poll_interval seconds for ticker + funding (contractSize
fetched once at first poll and cached).

IMPORTANT MEXC semantics:
- `amount24` is USDT-denominated turnover (matches volume24 × lastPrice ×
  contractSize within rounding) — pass through as vol24, do NOT multiply.
- `volume24` and `holdVol` are CONTRACT COUNTS; to get USDT we multiply
  by `contractSize × price`. contractSize varies per symbol (probe shows
  0.001 for SPACEX_USDT/OPENAI_USDT/ANTHROPIC_USDT, 0.01 for
  SPCXSTOCK_USDT) so we cannot hard-code it; we fetch once at startup
  from /contract/detail and cache per-adapter-instance.
- `fundingRate=0` on the valuation perps (SPACEX/OPENAI/ANTHROPIC_USDT)
  is the index-anchored convention (similar to OKX's fixed 0% funding);
  SPCXSTOCK_USDT runs a normal funding mechanism (probe rate 5e-05).
- 8h funding interval (collectCycle=8 on /funding_rate)."""

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

REST_BASE = "https://contract.mexc.com"

# (asset_id, exchange) -> MEXC symbol. The `exchange` key disambiguates the
# two SPACEX variants — "mexc" routes to the 1B-basis valuation perp
# (SPACEX_USDT) and "mexc-stock" routes to the stock-style perp
# (SPCXSTOCK_USDT). Same trick as bingx/bingx-prestocks.
_ASSET_TO_MEXC_SYMBOLS: dict[str, dict[str, str]] = {
    "SPACEX":    {"mexc": "SPACEX_USDT", "mexc-stock": "SPCXSTOCK_USDT"},
    "OPENAI":    {"mexc": "OPENAI_USDT"},
    "ANTHROPIC": {"mexc": "ANTHROPIC_USDT"},
}

POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.mexc")


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


class MEXCAdapter(ExchangeAdapter):
    exchange_id = "mexc"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None
        # Cached contractSize per symbol (fetched once at first poll).
        # MEXC contractSize varies (0.001 vs 0.01), so we cannot hard-code it.
        self._contract_size: dict[str, float] = {}

    async def discover(self, asset_id: str) -> DiscoverResult:
        # Discovery is exchange-key agnostic: try every known symbol for
        # this asset and return the first that responds. The supervisor
        # registers per-market so the right (exchange, symbol) pair is
        # already established by markets.json; discover() is only used by
        # scripts/calibrate.py for sanity checks.
        sym_by_key = _ASSET_TO_MEXC_SYMBOLS.get(asset_id.upper(), {})
        if not sym_by_key:
            return DiscoverResult(
                found=False,
                error=f"no MEXC symbol mapping for asset_id={asset_id}",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for symbol in sym_by_key.values():
                    ticker = await self._fetch_ticker(client, symbol)
                    if ticker is None:
                        continue
                    last_price = _to_float(ticker.get("lastPrice"))
                    return DiscoverResult(
                        found=True,
                        symbol=symbol,
                        last_price=last_price,
                        detail={
                            "indexPrice": ticker.get("indexPrice"),
                            "fairPrice": ticker.get("fairPrice"),
                            "amount24": ticker.get("amount24"),
                            "holdVol": ticker.get("holdVol"),
                            "fundingRate": ticker.get("fundingRate"),
                        },
                    )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")
        return DiscoverResult(found=False, error="no candidate returned a ticker")

    async def _fetch_ticker(self, client: httpx.AsyncClient, symbol: str) -> dict | None:
        r = await client.get(
            f"{REST_BASE}/api/v1/contract/ticker",
            params={"symbol": symbol},
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or not body.get("success"):
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None

    async def _fetch_contract_size(self, client: httpx.AsyncClient, symbol: str) -> float | None:
        # /contract/detail returns a list — even when filtering by symbol,
        # the field is `data: [{...}]`. Fetch once and cache; contractSize
        # is metadata, not state, so caching it for the lifetime of the
        # adapter is safe.
        try:
            r = await client.get(
                f"{REST_BASE}/api/v1/contract/detail",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or not body.get("success"):
                return None
            data = body.get("data")
            if isinstance(data, list):
                data = data[0] if data else None
            if not isinstance(data, dict):
                return None
            return _to_float(data.get("contractSize"))
        except Exception:  # noqa: BLE001
            return None

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        symbol = market.symbol

        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    # Lazy-fetch contractSize on first iteration (and refetch
                    # if a previous poll set it to None due to error).
                    if self._contract_size.get(symbol) is None:
                        cs = await self._fetch_contract_size(client, symbol)
                        if cs is not None and cs > 0:
                            self._contract_size[symbol] = cs
                            log.info(
                                "mexc_contract_size_cached",
                                symbol=symbol,
                                contract_size=cs,
                            )

                    ticker = await self._fetch_ticker(client, symbol)
                    if ticker is None:
                        raise RuntimeError("ticker fetch returned None")
                    last_price = _to_float(ticker.get("lastPrice"))
                    if last_price is None:
                        raise RuntimeError("missing lastPrice")

                    bid = _to_float(ticker.get("bid1"))
                    ask = _to_float(ticker.get("ask1"))

                    # amount24 is already USDT — pass through, no multiply.
                    vol24 = _to_float(ticker.get("amount24"))

                    # OI: holdVol is in CONTRACTS; convert to USDT via
                    # contractSize × price. If contractSize cache is empty
                    # (first poll errored) we drop OI for this tick rather
                    # than emit a wrong-scale figure.
                    hold_vol = _to_float(ticker.get("holdVol"))
                    cs = self._contract_size.get(symbol)
                    oi_usd: float | None
                    if hold_vol is not None and cs is not None and cs > 0:
                        oi_usd = hold_vol * cs * last_price
                    else:
                        oi_usd = None

                    funding_rate = _to_float(ticker.get("fundingRate"))

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
                        # MEXC perps settle every 8h (collectCycle=8 on
                        # /funding_rate endpoint, confirmed at probe).
                        funding_interval_hours=8,
                        timestamp=ts,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "mexc_poll_error",
                        symbol=symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke: `python -m app.adapters.mexc`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = MEXCAdapter()
        for asset in ("SPACEX", "OPENAI", "ANTHROPIC"):
            result = await adapter.discover(asset)
            print(f"{asset:10s} discover: found={result.found} symbol={result.symbol} last={result.last_price}")
            if not result.found:
                print(f"           error: {result.error}")

        market = Market(
            asset_id="OPENAI",
            exchange="mexc",
            symbol="OPENAI_USDT",
            venue="CEX",
            structure="perp",
            share_ratio=2.4,
            ratio_source="market-fitted",
            confidence=30,
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
