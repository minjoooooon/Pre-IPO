"""Hyperliquid adapter.

NOTE (P1.5 finding, 2026-05-18): SpaceX (`SPCX`) is NOT in the core Hyperliquid
perp universe (`/info {type: meta}` returns 230 markets, no SPCX match — only
`SPX` which is a different asset). The SPCX market referenced in news reports
is a **builder-deployed market on Hyperliquid via HIP-3** (Trade.xyz / Ventuals
deployer), which routes through a different namespace.

This adapter therefore reports `found=False` for SPACEX in core API and the
calibration step marks the venue as disabled. Future work: implement HIP-3
builder-deployed market routing (likely requires fetching the deployer address
and querying their custom subgraph)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from ..models import Market, Tick
from .base import DiscoverResult, ExchangeAdapter

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"


# user-facing asset_id (uppercase) → expected core-universe symbol on Hyperliquid
_ASSET_TO_HL_NAME: dict[str, str] = {
    "SPACEX": "SPCX",
}


class HyperliquidAdapter(ExchangeAdapter):
    exchange_id = "hyperliquid"

    async def discover(self, asset_id: str) -> DiscoverResult:
        target = _ASSET_TO_HL_NAME.get(asset_id.upper())
        if target is None:
            return DiscoverResult(
                found=False,
                error=f"no Hyperliquid symbol mapping for asset_id={asset_id}",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(INFO_URL, json={"type": "metaAndAssetCtxs"})
                r.raise_for_status()
                data = r.json()
        except Exception as e:  # noqa: BLE001 — surface as error string
            return DiscoverResult(found=False, error=f"http error: {e!r}")

        if not (isinstance(data, list) and len(data) >= 2):
            return DiscoverResult(found=False, error="unexpected response shape")

        universe = data[0].get("universe", []) if isinstance(data[0], dict) else []
        asset_ctxs = data[1] if isinstance(data[1], list) else []
        for i, u in enumerate(universe):
            if isinstance(u, dict) and u.get("name", "").upper() == target:
                ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                last_price: float | None = None
                # markPx is a string per Hyperliquid docs
                for key in ("markPx", "midPx", "oraclePx"):
                    raw = ctx.get(key) if isinstance(ctx, dict) else None
                    if raw is not None:
                        try:
                            last_price = float(raw)
                            break
                        except (TypeError, ValueError):
                            continue
                return DiscoverResult(
                    found=True,
                    symbol=target,
                    last_price=last_price,
                    detail={"universe_index": i, "ctx_keys": list(ctx.keys()) if isinstance(ctx, dict) else []},
                )

        return DiscoverResult(
            found=False,
            symbol=target,
            error=f"{target} not in core perp universe (size={len(universe)}); likely HIP-3 builder-deployed, routing not yet implemented",
        )

    async def stream(self, market: Market) -> AsyncIterator[Tick]:
        """Not yet implemented — pending HIP-3 builder-deployed market routing.

        P2 will implement this for core-universe markets once we have a confirmed
        SpaceX listing path; for SPCX specifically the supervisor will keep the
        adapter in `disabled` state."""
        raise NotImplementedError(
            "Hyperliquid streaming for SPCX requires HIP-3 routing (see P12+)"
        )
        # required to make this an async generator
        yield  # type: ignore[unreachable]  # noqa: B901
