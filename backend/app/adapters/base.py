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
