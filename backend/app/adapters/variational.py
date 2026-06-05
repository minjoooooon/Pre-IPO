"""Variational adapter — Variational Omni perp DEX via the public stats REST.

Background:
  Variational Omni is a perpetual-futures DEX (450+ markets). It exposes a
  public, no-auth market-statistics endpoint:

    GET https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats
       → platform-wide metrics + a per-listing collection. Each listing
         carries (per the published API docs): ticker/name, mark price,
         index price, last/quoted price, 24h volume, open interest, funding
         rate, and bid/ask quotes. Prices/volumes are USDC-denominated;
         funding rates are decimals.

  Rate limits: 10 req / 10 s per IP, 1000 req/min global → a 5 s poll is
  comfortably inside the budget.

⚠️ SCHEMA NOTE: the Variational docs are behind Cloudflare and could not be
   fetched during development, so the EXACT JSON key names below are not yet
   confirmed against a live response. The parser is therefore defensive: it
   probes a list of candidate keys for each field, and discover() dumps the
   real keys it sees into DiscoverResult.detail. Run the standalone probe
   (`python -m app.adapters.variational`) on a host with open network to
   pin the keys down, then trim the candidate lists.

The Variational symbol for the pre-IPO markets is not hard-coded — discover()
enumerates the live universe and matches a small candidate set per asset."""

from __future__ import annotations

import asyncio
import io
import sys
import time
from collections.abc import AsyncIterator, Iterable

import httpx
import structlog

from ..config import settings
from ..models import Market, Tick
from .base import DiscoverResult, ExchangeAdapter

STATS_URL = (
    "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats"
)

POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.variational")


# asset_id (uppercase) → candidate tickers Variational might use.
_ASSET_SYMBOL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "SPACEX": ("SPACEX", "SPCX", "SPX"),
    "OPENAI": ("OPENAI", "OPAI"),
    "ANTHROPIC": ("ANTHROPIC", "ANTH"),
}

# Candidate keys for the per-listing collection inside the stats payload, and
# for each field on a listing object. Confirm + trim on the first live probe.
_LISTING_COLLECTION_KEYS = ("listings", "markets", "per_listing", "listing_stats", "stats")
_SYMBOL_KEYS = ("ticker", "symbol", "name", "instrument", "market")
_PRICE_KEYS = ("mark_price", "mark", "last_price", "last", "quoted_price", "price", "index_price")
_VOL_KEYS = ("volume_24h", "volume24h", "vol_24h", "daily_volume", "volume")
_OI_KEYS = ("open_interest", "oi", "open_interest_usd")
_FUNDING_KEYS = ("funding_rate", "funding", "funding_rate_1h", "funding_rate_8h")
_BID_KEYS = ("bid", "bid_price", "best_bid")
_ASK_KEYS = ("ask", "ask_price", "best_ask")


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _get_first(obj: dict, keys: Iterable[str]) -> object:
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return None


class VariationalAdapter(ExchangeAdapter):
    exchange_id = "variational"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def _fetch_listings(self, client: httpx.AsyncClient) -> list[dict]:
        """One /metadata/stats call → the list of per-listing stat dicts.

        Handles both shapes defensively: a top-level list, or a dict that
        nests the listings under one of _LISTING_COLLECTION_KEYS."""
        r = await client.get(STATS_URL)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for key in _LISTING_COLLECTION_KEYS:
                coll = data.get(key)
                if isinstance(coll, list):
                    return [d for d in coll if isinstance(d, dict)]
                if isinstance(coll, dict):
                    # map keyed by symbol → flatten values, injecting the key
                    out = []
                    for sym, v in coll.items():
                        if isinstance(v, dict):
                            v.setdefault("symbol", sym)
                            out.append(v)
                    if out:
                        return out
        return []

    @staticmethod
    def _symbol_of(d: dict) -> str:
        return str(_get_first(d, _SYMBOL_KEYS) or "").strip()

    @classmethod
    def _find_by_symbol(cls, listings: list[dict], symbol: str) -> dict | None:
        target = symbol.strip().upper()
        for d in listings:
            if cls._symbol_of(d).upper() == target:
                return d
        return None

    @classmethod
    def _tick_from_listing(cls, market_id: str, d: dict, ts: float) -> Tick | None:
        last_price = _to_float(_get_first(d, _PRICE_KEYS))
        if last_price is None:
            return None
        return Tick(
            market_id=market_id,
            token_price=last_price,
            bid=_to_float(_get_first(d, _BID_KEYS)),
            ask=_to_float(_get_first(d, _ASK_KEYS)),
            vol24=_to_float(_get_first(d, _VOL_KEYS)),
            oi=_to_float(_get_first(d, _OI_KEYS)),
            funding_rate=_to_float(_get_first(d, _FUNDING_KEYS)),
            funding_interval_hours=None,  # confirm cadence on live probe
            timestamp=ts,
        )

    async def discover(self, asset_id: str) -> DiscoverResult:
        candidates = _ASSET_SYMBOL_CANDIDATES.get(asset_id.upper())
        if not candidates:
            return DiscoverResult(
                found=False,
                error=f"no Variational symbol candidates for asset_id={asset_id}",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                listings = await self._fetch_listings(client)
                if not listings:
                    return DiscoverResult(
                        found=False,
                        error="empty /metadata/stats listings (check collection key)",
                    )
                for cand in candidates:
                    d = self._find_by_symbol(listings, cand)
                    if d is not None:
                        return DiscoverResult(
                            found=True,
                            symbol=self._symbol_of(d),
                            last_price=_to_float(_get_first(d, _PRICE_KEYS)),
                            detail={"listing_keys": list(d), "sample": d},
                        )
                listed = sorted({self._symbol_of(d) for d in listings if self._symbol_of(d)})
                return DiscoverResult(
                    found=False,
                    error=(
                        f"{asset_id} not in Variational universe "
                        f"(tried {candidates}; {len(listed)} markets listed)"
                    ),
                    detail={"sample_symbols": listed[:40]},
                )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        backoff = settings.reconnect_initial_seconds
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    listings = await self._fetch_listings(client)
                    d = self._find_by_symbol(listings, market.symbol)
                    if d is None:
                        raise RuntimeError(
                            f"symbol {market.symbol!r} not in Variational universe"
                        )
                    ts = time.time()
                    tick = self._tick_from_listing(market.market_id, d, ts)
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
                        "variational_poll_error",
                        exchange=market.exchange,
                        symbol=market.symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone probe: `python -m app.adapters.variational [ASSET_ID ...]`
# Run on a host with open network to confirm listings, capture the exact
# symbols + JSON keys, and read calibration anchor prices.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = VariationalAdapter()
        assets = sys.argv[1:] or ["SPACEX", "OPENAI", "ANTHROPIC"]
        for asset_id in assets:
            d = await adapter.discover(asset_id)
            print(
                f"discover {asset_id}: found={d.found} symbol={d.symbol} "
                f"last={d.last_price}"
            )
            if d.detail:
                print(f"  detail: {d.detail}")
            if not d.found:
                print(f"  error: {d.error}")

    asyncio.run(_smoke())
