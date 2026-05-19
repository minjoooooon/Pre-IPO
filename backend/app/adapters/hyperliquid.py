"""Hyperliquid adapter — HIP-3 builder-deployed perps via dex-aware /info.

Background (2026-05-20 probe):
  SPCX is NOT in the core perp universe (POST /info {"type":"meta"} returns
  230 markets, no SPCX). It lives on HIP-3 builder-deployed dexes:

    POST /info {"type":"perpDexs"}  → list of 9 builder entries.
      Builder DEXes carrying a SpaceX market in May 2026:
        - dex="xyz",  fullName="XYZ"        → coin "xyz:SPCX"
            (alongside stock-style perps: AAPL, TSLA, NVDA, GOLD, ...)
        - dex="vntl", fullName="Ventuals"   → coin "vntl:SPACEX"
            (private-company valuation perps: SPACEX, OPENAI, ANTHROPIC, ...)

Live data is fetched via metaAndAssetCtxs (gives funding/OI/vol on top of
mid price, exactly like core-perp ctx):

    POST /info {"type":"metaAndAssetCtxs","dex":"<dex>"}
       → [
           {"universe":[{"name":"xyz:SPCX",...}, ...]},
           [{"funding":"...", "openInterest":"...", "markPx":"...",
             "midPx":"...", "oraclePx":"...", "dayNtlVlm":"...", ...}, ...]
         ]

Universe and ctxs share the same index. The "name" field is fully qualified
(includes the dex prefix), so we match market.symbol exactly.

The adapter parses market.symbol of the form "<dex>:<coin>" to extract the
dex; the full symbol is the lookup key in the universe."""

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

INFO_URL = "https://api.hyperliquid.xyz/info"

POLL_INTERVAL_SECONDS = 5.0

log = structlog.get_logger("app.adapters.hyperliquid")


# user-facing asset_id (uppercase) → expected symbol per builder dex.
# Used only by discover() to surface a sensible default when calibrate.py
# is run without an explicit symbol. The Market entry in markets.json is the
# real source of truth; this map just bootstraps new assets.
_ASSET_TO_HL_SYMBOL_BY_EXCHANGE: dict[tuple[str, str], str] = {
    ("SPACEX", "hl-xyz"):  "xyz:SPCX",
    ("SPACEX", "hl-vntl"): "vntl:SPACEX",
    ("OPENAI", "hl-vntl"): "vntl:OPENAI",
    ("ANTHROPIC", "hl-vntl"): "vntl:ANTHROPIC",
}


def _to_float(x: object) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _split_symbol(symbol: str) -> tuple[str, str] | None:
    """'xyz:SPCX' → ('xyz', 'xyz:SPCX'). The full 'dex:coin' string is the
    allMids dict key, so we keep it as-is for lookup; the dex prefix is what
    the /info call needs in its dex parameter."""
    if not symbol or ":" not in symbol:
        return None
    dex = symbol.split(":", 1)[0].strip()
    if not dex:
        return None
    return dex, symbol


class HyperliquidAdapter(ExchangeAdapter):
    # The supervisor uses adapter.exchange_id only for log tagging; markets'
    # actual routing key is their `exchange` field. We default to "hyperliquid"
    # so logs say "hyperliquid_poll_error" regardless of builder.
    exchange_id = "hyperliquid"

    def __init__(self) -> None:
        self._reconnect_count = 0
        self._last_tick_at: float | None = None

    async def _fetch_ctx(
        self, client: httpx.AsyncClient, dex: str, coin_key: str
    ) -> dict | None:
        """One metaAndAssetCtxs call → the ctx dict matching coin_key, or None
        if the coin isn't in that dex's universe right now."""
        r = await client.post(
            INFO_URL, json={"type": "metaAndAssetCtxs", "dex": dex}
        )
        r.raise_for_status()
        data = r.json()
        if not (isinstance(data, list) and len(data) >= 2):
            return None
        universe = data[0].get("universe", []) if isinstance(data[0], dict) else []
        ctxs = data[1] if isinstance(data[1], list) else []
        for i, u in enumerate(universe):
            if isinstance(u, dict) and u.get("name") == coin_key:
                if i < len(ctxs) and isinstance(ctxs[i], dict):
                    return ctxs[i]
                return None
        return None

    async def discover(self, asset_id: str) -> DiscoverResult:
        guesses = [
            sym for (aid, _exch), sym in _ASSET_TO_HL_SYMBOL_BY_EXCHANGE.items()
            if aid == asset_id.upper()
        ]
        if not guesses:
            return DiscoverResult(
                found=False,
                error=f"no Hyperliquid HIP-3 mapping for asset_id={asset_id}",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for symbol in guesses:
                    parsed = _split_symbol(symbol)
                    if not parsed:
                        continue
                    dex, coin_key = parsed
                    ctx = await self._fetch_ctx(client, dex, coin_key)
                    if ctx is None:
                        continue
                    # Prefer markPx (the contract's mark) for the snapshot;
                    # fall back to midPx then oraclePx.
                    last_price = None
                    for k in ("markPx", "midPx", "oraclePx"):
                        last_price = _to_float(ctx.get(k))
                        if last_price is not None:
                            break
                    return DiscoverResult(
                        found=True,
                        symbol=symbol,
                        last_price=last_price,
                        detail={"dex": dex, "coin_key": coin_key, "ctx_keys": list(ctx)},
                    )
        except Exception as e:  # noqa: BLE001
            return DiscoverResult(found=False, error=f"http error: {e!r}")

        return DiscoverResult(
            found=False,
            error=f"{asset_id} not found in any tried HIP-3 dex (guesses={guesses})",
        )

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        parsed = _split_symbol(market.symbol)
        if not parsed:
            raise RuntimeError(
                f"Hyperliquid symbol must be 'dex:COIN' form, got: {market.symbol!r}"
            )
        dex, coin_key = parsed

        backoff = settings.reconnect_initial_seconds
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    ctx = await self._fetch_ctx(client, dex, coin_key)
                    if ctx is None:
                        raise RuntimeError(
                            f"coin {coin_key!r} not in dex {dex!r} universe"
                        )

                    # markPx is the canonical traded price for the perp.
                    # midPx is also fine; oraclePx is the index, not a trade.
                    last_price = None
                    for k in ("markPx", "midPx", "oraclePx"):
                        last_price = _to_float(ctx.get(k))
                        if last_price is not None:
                            break
                    if last_price is None:
                        raise RuntimeError(
                            f"no usable price in ctx for {coin_key} "
                            f"(keys: {list(ctx)})"
                        )

                    # Hyperliquid funding is per-hour (decimal fraction).
                    # Other adapters report whatever cadence the venue uses;
                    # the FE formats consistently per-period, so we don't
                    # rescale here.
                    funding = _to_float(ctx.get("funding"))
                    oi = _to_float(ctx.get("openInterest"))
                    # dayNtlVlm is USD-denominated 24h notional volume.
                    vol24 = _to_float(ctx.get("dayNtlVlm"))

                    ts = time.time()
                    self._last_tick_at = ts
                    backoff = settings.reconnect_initial_seconds
                    yield Tick(
                        market_id=market.market_id,
                        token_price=last_price,
                        bid=None,
                        ask=None,
                        vol24=vol24,
                        oi=oi,
                        funding_rate=funding,
                        timestamp=ts,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._reconnect_count += 1
                    log.warning(
                        "hyperliquid_poll_error",
                        exchange=market.exchange,
                        symbol=market.symbol,
                        backoff=backoff,
                        error=repr(e),
                        reconnect_count=self._reconnect_count,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, settings.reconnect_max_seconds)


# ---------------------------------------------------------------------------
# Standalone smoke test: `python -m app.adapters.hyperliquid`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _smoke() -> None:
        adapter = HyperliquidAdapter()
        d = await adapter.discover("SPACEX")
        print(f"discover: found={d.found} symbol={d.symbol} last={d.last_price}")
        if not d.found:
            print(f"  error: {d.error}")
            return

        market = Market(
            asset_id="SPACEX",
            exchange="hl-xyz",
            symbol="xyz:SPCX",
            venue="DEX",
            structure="perp",
            share_ratio=1.0,
            ratio_source="market-fitted",
            confidence=30,
        )
        start = time.time()
        n = 0
        async for tick in adapter.stream(market):
            n += 1
            print(f"[{n:03d}] t={tick.timestamp:.1f} px={tick.token_price}")
            if time.time() - start > 12 or n >= 2:
                break

    asyncio.run(_smoke())
