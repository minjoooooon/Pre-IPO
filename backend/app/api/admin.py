"""Admin REST endpoints for live config edits.

POST /api/admin/asset/{asset_id}/shares
    body: {"fully_diluted_shares": float, "source": str, "confidence": int}
POST /api/admin/market/{asset_id}/{exchange}/ratio
    body: {"share_ratio": float, "ratio_source": str,
           "ratio_reference_fdv": float | None, "confidence": int}

Writes are persisted to assets.json / markets.json atomically (tmp file +
replace) and the in-memory Registry is updated so subsequent ticks use the
new values without restart. After each write we broadcast a `config-update`
WS message so live FE clients re-render with the new metadata immediately
instead of showing stale FDV until the next snapshot."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..models import Asset, Market
from ..registry import get_registry

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UpdateSharesBody(BaseModel):
    fully_diluted_shares: float = Field(gt=0)
    source: str = Field(min_length=1)
    confidence: int = Field(ge=0, le=100)


class UpdateRatioBody(BaseModel):
    share_ratio: float = Field(gt=0)
    ratio_source: str = Field(min_length=1)
    ratio_reference_fdv: float | None = None
    confidence: int = Field(ge=0, le=100)


async def _broadcast_config_update(request: Request) -> None:
    aggregator = getattr(request.app.state, "aggregator", None)
    if aggregator is None:
        return
    try:
        await aggregator.broadcast_config_update()
    except Exception:  # noqa: BLE001
        # never fail the admin write because a broadcast hiccupped
        pass


@router.post("/asset/{asset_id}/shares", response_model=Asset)
async def update_asset_shares(asset_id: str, body: UpdateSharesBody, request: Request) -> Asset:
    try:
        result = get_registry().update_asset_shares(
            asset_id=asset_id,
            fully_diluted_shares=body.fully_diluted_shares,
            source=body.source,
            confidence=body.confidence,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await _broadcast_config_update(request)
    return result


@router.post("/market/{asset_id}/{exchange}/ratio", response_model=Market)
async def update_market_ratio(
    asset_id: str, exchange: str, body: UpdateRatioBody, request: Request
) -> Market:
    try:
        result = get_registry().update_market_ratio(
            asset_id=asset_id,
            exchange=exchange,
            share_ratio=body.share_ratio,
            ratio_source=body.ratio_source,
            ratio_reference_fdv=body.ratio_reference_fdv,
            confidence=body.confidence,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await _broadcast_config_update(request)
    return result
