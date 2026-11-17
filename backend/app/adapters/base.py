"""Base class for exchange adapters.

Two abstract responsibilities:
  - discover(asset_id): one-shot REST call to confirm the market exists on the
    venue, return canonical symbol and a snapshot price. Used by P1.5 calibration
    and adapter startup.
  - stream(market): long-running async iterator emitting Tick events from a
    WebSocket (or REST polling) subscription. Implementations must:
      * survive parse errors per-message (try/except inside the loop)
      * survive connection drops via exponential backoff reconnect
      * emit ticks even when partial data is available (e.g. price without OI)"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..models import Market, Tick


@dataclass(slots=True)
class DiscoverResult:
    """Returned by adapter.discover()."""

    found: bool
    symbol: str | None = None       # canonical exchange symbol (e.g. SPACEX-USDT-SWAP)
    last_price: float | None = None # current/last reference price for calibration
    detail: dict | None = None      # raw response excerpt (for audit)
    error: str | None = None        # human-readable failure reason


class ExchangeAdapter(ABC):
    exchange_id: str = ""           # must be overridden by subclass
    stale_threshold_seconds: int = 60

    @abstractmethod
    async def discover(self, asset_id: str) -> DiscoverResult:
        """Verify the market exists. Called at startup + by scripts/calibrate.py."""

    @abstractmethod
    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        """Yield ticks indefinitely. Caller wraps in supervisor for crash isolation."""


def finite_number(value):
    import math
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


class PublicRestAdapter(ExchangeAdapter):
    """Small polling lifecycle; supervisor owns reconnect/backoff on failure."""
    symbols: dict[str, str] = {}

    async def discover(self, asset_id: str) -> DiscoverResult:
        import httpx
        symbol = self.symbols.get(asset_id.upper())
        if not symbol:
            return DiscoverResult(found=False, error="No verified symbol")
        market = Market(asset_id=asset_id, exchange=self.exchange_id, symbol=symbol,
                        venue="CEX", structure="perp", ratio_source="uncalibrated", confidence=0)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                tick = await self.fetch_tick(client, market)
            return DiscoverResult(found=True, symbol=symbol, last_price=tick.token_price)
        except Exception as exc:
            return DiscoverResult(found=False, symbol=symbol, error=str(exc))

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        import asyncio
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                yield await self.fetch_tick(client, market)
                await asyncio.sleep(5)

    @abstractmethod
    async def fetch_tick(self, client, market: Market) -> Tick:
        """Validate current contract status and parse a public snapshot."""
