"""BitMart adapter — Contract (Futures) pre-IPO instruments.

BitMart lists pre-IPO synthetics on its Contract platform with
`tradfi_info` populated (market_group=US_MARKET). Probe 2026-05-21
returned three relevant contracts:

  SPACEXUSDT      1B-basis valuation perp ($2404, OKX/hl-vntl family)
  OPENAIUSDT      1B-basis valuation perp ($1208, deepest OPENAI venue)
  ANTHROPICUSDT   1B-basis valuation perp ($1282, deepest ANTHROPIC venue)

Endpoints (public, no auth):
  GET https://api-cloud-v2.bitmart.com/contract/public/details?symbol=<sym>
    -> {"code":1000, "data":{"symbols":[{...}]}}
    Single record contains EVERYTHING we need except top-of-book:
      last_price, turnover_24h[USDT], open_interest_value[USDT],
      funding_rate, funding_interval_hours, index_price, contract_size,
      tradfi_info{market_session_status, ...}, status
  GET https://api-cloud-v2.bitmart.com/contract/public/depth?symbol=<sym>
    -> {"code":1000, "data":{"asks":[[price,qty,cum],...], "bids":[...]}}
    Used only for top-of-book bid/ask (separate call — details has no
    bid/ask fields).

REST poll every poll_interval seconds.

IMPORTANT BitMart semantics:
- `turnover_24h` IS USDT — passed through verbatim as vol24.
- `open_interest_value` IS USDT (already converted by BitMart from
  open_interest × contract_size × mark) — passed through verbatim as OI.
  We trust their figure even though it differs slightly from
  open_interest × contract_size × last_price (uses mark internally;
  ~10% drift is normal because the contract trades at a wide
  premium/discount vs index in thin pre-IPO orderbooks).
- `funding_interval_hours` is INCLUDED in the details response and
  varies per contract (probe shows 1h for BitMart pre-IPO perps —
  not the 8h that most CEX perps use). Reading it from the record
  rather than hard-coding 8 is essential for correct annualization
  in the FE."""

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

REST_BASE = "https://api-cloud-v2.bitmart.com"

_ASSET_TO_BITMART_SYMBOLS: dict[str, str] = {
    "SPACEX":    "SPACEXUSDT",
    "OPENAI":    "OPENAIUSDT",
    "ANTHROPIC": "ANTHROPICUSDT",
}

POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.bitmart")


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


class BitMartAdapter(ExchangeAdapter):
    exchange_id = "bitmart"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def discover(self, asset_id: str) -> DiscoverResult:
        symbol = _ASSET_TO_BITMART_SYMBOLS.get(asset_id.upper())
        if not symbol:
            return DiscoverResult(
                found=False,
                error=f"no BitMart symbol mapping for asset_id={asset_id}",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                detail = await self._fetch_detail(client, symbol)
                if detail is None:
                    return DiscoverResult(
                        found=False,
                        error="detail fetch returned None",
                    )
                last_price = _to_float(detail.get("last_price"))
                return DiscoverResult(
                    found=True,
                    symbol=symbol,
                    last_price=last_price,
                    detail={
                        "index_price": detail.get("index_price"),
                        "turnover_24h": detail.get("turnover_24h"),
                        "open_interest_value": detail.get("open_interest_value"),
                        "funding_rate": detail.get("funding_rate"),
                        "funding_interval_hours": detail.get("funding_interval_hours"),
                    },
                )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

    async def _fetch_detail(self, client: httpx.AsyncClient, symbol: str) -> dict | None:
        r = await client.get(
            f"{REST_BASE}/contract/public/details",
            params={"symbol": symbol},
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or body.get("code") != 1000:
            return None
        data = body.get("data", {})
        symbols = data.get("symbols", []) if isinstance(data, dict) else []
        return symbols[0] if symbols else None

    async def _fetch_top_of_book(
        self, client: httpx.AsyncClient, symbol: str
    ) -> tuple[float | None, float | None]:
        # Top bid/ask only — depth is best-effort. Returning (None, None)
        # on any failure lets the main tick still emit with price + vol +
        # OI from the details call.
        try:
            r = await client.get(
                f"{REST_BASE}/contract/public/depth",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or body.get("code") != 1000:
                return (None, None)
            data = body.get("data", {})
            bids = data.get("bids", []) if isinstance(data, dict) else []
            asks = data.get("asks", []) if isinstance(data, dict) else []
            top_bid = _to_float(bids[0][0]) if bids and bids[0] else None
            top_ask = _to_float(asks[0][0]) if asks and asks[0] else None
            return (top_bid, top_ask)
        except Exception:  # noqa: BLE001
            return (None, None)

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        symbol = market.symbol

        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    detail = await self._fetch_detail(client, symbol)
                    if detail is None:
                        raise RuntimeError("detail fetch returned None")
                    last_price = _to_float(detail.get("last_price"))
                    if last_price is None:
                        raise RuntimeError("missing last_price")

                    # turnover_24h IS USDT — passthrough.
                    vol24 = _to_float(detail.get("turnover_24h"))

                    # open_interest_value IS USDT — passthrough (trust the
                    # venue's number, computed against their mark internally).
                    oi_usd = _to_float(detail.get("open_interest_value"))

                    funding_rate = _to_float(detail.get("funding_rate"))

                    # funding_interval_hours is per-contract on BitMart
                    # (1h on probe — NOT the 8h default we use for most
                    # CEX perps). Read it from the record.
                    funding_int_hours = _to_int(detail.get("funding_interval_hours"))

                    # Top-of-book — best effort, separate endpoint.
                    bid, ask = await self._fetch_top_of_book(client, symbol)

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
                        funding_interval_hours=funding_int_hours,
                        timestamp=ts,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "bitmart_poll_error",
                        symbol=symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke: `python -m app.adapters.bitmart`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = BitMartAdapter()
        for asset in ("SPACEX", "OPENAI", "ANTHROPIC"):
            result = await adapter.discover(asset)
            print(f"{asset:10s} discover: found={result.found} symbol={result.symbol} last={result.last_price}")
            if not result.found:
                print(f"           error: {result.error}")

        market = Market(
            asset_id="OPENAI",
            exchange="bitmart",
            symbol="OPENAIUSDT",
            venue="CEX",
            structure="perp",
            share_ratio=2.4,
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
