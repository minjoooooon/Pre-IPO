"""Lighter adapter — zkLighter perp DEX via the public orderBookDetails REST.

Background:
  Lighter (zkLighter) is an order-book perp DEX. Its public, no-auth REST
  exposes every market's live stats in one call:

    GET https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails
       → {
           "code": 200,
           "order_book_details": [ <PerpsOrderBookDetail>, ... ],
           "spot_order_book_details": [ ... ]
         }

  Each PerpsOrderBookDetail (fields confirmed against the elliottech/
  lighter-python SDK model) carries:
    symbol                    e.g. "BTC", "ETH", ...  (the routing key)
    market_id                 int
    status                    "active" / ...
    last_trade_price          float  (USD/USDC quote)
    daily_quote_token_volume  float  (24h notional in quote = USD)
    daily_base_token_volume   float
    open_interest             float  (base units — see OI note below)
    daily_price_change        float

  There is NO live funding-rate field on this model (only funding_clamp_*
  and base_interest_rate). Lighter exposes funding via a separate
  /api/v1/funding* tree; we leave funding_rate=None on the first cut to
  keep the poll a single request, and a live VPS probe can decide whether
  the extra call is worth it. funding_interval_hours is therefore None too.

OI note: `open_interest` is base-denominated (contract/share units), not
USD — same convention as Hyperliquid. We multiply by last_trade_price so
the cross-venue size proxy stays on consistent USD units (Slice 5 F64).
Confirm the denomination on the first live probe.

The Lighter symbol for the pre-IPO markets (SPACEX/OPENAI/ANTHROPIC) is NOT
hard-coded — discover() enumerates the live universe and matches against a
small candidate set per asset, so we record whatever Lighter actually uses
(e.g. "SPX", "SPACEX", "SPCX") rather than guessing one string."""

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

ORDER_BOOK_DETAILS_URL = "https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails"

POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.lighter")


# asset_id (uppercase) → candidate symbols Lighter might list the market under.
# discover() matches case-insensitively; the first hit wins. This is only a
# bootstrap aid — markets.json `symbol` is the source of truth once added.
_ASSET_SYMBOL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "SPACEX": ("SPACEX", "SPCX", "SPX"),
    "OPENAI": ("OPENAI", "OPAI"),
    "ANTHROPIC": ("ANTHROPIC", "ANTH"),
}


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class LighterAdapter(ExchangeAdapter):
    exchange_id = "lighter"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def _fetch_details(self, client: httpx.AsyncClient) -> list[dict]:
        """One orderBookDetails call → the list of perp market detail dicts."""
        r = await client.get(ORDER_BOOK_DETAILS_URL)
        r.raise_for_status()
        data = r.json()
        details = data.get("order_book_details") if isinstance(data, dict) else None
        return details if isinstance(details, list) else []

    @staticmethod
    def _find_by_symbol(details: list[dict], symbol: str) -> dict | None:
        target = symbol.strip().upper()
        for d in details:
            if isinstance(d, dict) and str(d.get("symbol", "")).strip().upper() == target:
                return d
        return None

    @staticmethod
    def _tick_from_detail(market_id: str, d: dict, ts: float) -> Tick | None:
        last_price = _to_float(d.get("last_trade_price"))
        if last_price is None:
            return None
        vol24 = _to_float(d.get("daily_quote_token_volume"))
        oi_base = _to_float(d.get("open_interest"))
        # Base-denominated OI → USD (see module docstring OI note).
        oi_usd = oi_base * last_price if oi_base is not None else None
        return Tick(
            market_id=market_id,
            token_price=last_price,
            bid=None,
            ask=None,
            vol24=vol24,
            oi=oi_usd,
            funding_rate=None,
            funding_interval_hours=None,
            timestamp=ts,
        )

    async def discover(self, asset_id: str) -> DiscoverResult:
        candidates = _ASSET_SYMBOL_CANDIDATES.get(asset_id.upper())
        if not candidates:
            return DiscoverResult(
                found=False,
                error=f"no Lighter symbol candidates for asset_id={asset_id}",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                details = await self._fetch_details(client)
                if not details:
                    return DiscoverResult(found=False, error="empty order_book_details")
                listed = {str(d.get("symbol", "")).strip().upper() for d in details}
                for cand in candidates:
                    d = self._find_by_symbol(details, cand)
                    if d is not None:
                        return DiscoverResult(
                            found=True,
                            symbol=str(d.get("symbol")),
                            last_price=_to_float(d.get("last_trade_price")),
                            detail={
                                "market_id": d.get("market_id"),
                                "status": d.get("status"),
                                "daily_quote_token_volume": d.get("daily_quote_token_volume"),
                                "open_interest": d.get("open_interest"),
                                "detail_keys": list(d),
                            },
                        )
                return DiscoverResult(
                    found=False,
                    error=(
                        f"{asset_id} not in Lighter universe "
                        f"(tried {candidates}; {len(listed)} markets listed)"
                    ),
                )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    details = await self._fetch_details(client)
                    d = self._find_by_symbol(details, market.symbol)
                    if d is None:
                        raise RuntimeError(
                            f"symbol {market.symbol!r} not in Lighter universe"
                        )
                    ts = time.time()
                    tick = self._tick_from_detail(market.market_id, d, ts)
                    if tick is None:
                        raise RuntimeError(
                            f"no usable price for {market.symbol} (keys: {list(d)})"
                        )
                    self._last_tick_at = ts
                    backoff = settings.reconnect_initial_seconds
                    yield tick
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "lighter_poll_error",
                        exchange=market.exchange,
                        symbol=market.symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone probe: `python -m app.adapters.lighter [ASSET_ID ...]`
# Run this on a host with open network (e.g. the VPS) to confirm the markets
# exist, capture the exact symbols, and read calibration anchor prices.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = LighterAdapter()
        assets = sys.argv[1:] or ["SPACEX", "OPENAI", "ANTHROPIC"]
        for asset_id in assets:
            d = await adapter.discover(asset_id)
            print(
                f"discover {asset_id}: found={d.found} symbol={d.symbol} "
                f"last={d.last_price}"
            )
            if not d.found:
                print(f"  error: {d.error}")
                continue
            print(f"  detail: {d.detail}")

    asyncio.run(_smoke())
