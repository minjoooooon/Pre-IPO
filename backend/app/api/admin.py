"""Admin REST endpoints for live config edits.

POST /api/admin/asset/{asset_id}/shares
    headers: X-Admin-Token: <settings.admin_token>
    body:    {"fully_diluted_shares": float (>0, finite),
              "source": str (1..1024),
              "source_url": str | null (optional, <=2048),
              "confidence": int 0..100}
POST /api/admin/market/{asset_id}/{exchange}/ratio
    headers: X-Admin-Token: <settings.admin_token>
    body:    {"share_ratio": float (>0, finite),
              "ratio_source": one of RatioSource literals,
              "ratio_reference_fdv": float (>0, finite) | null,
              "confidence": int 0..100}

Writes are persisted to assets.json / markets.json atomically (tmp file +
replace + fsync + Windows-retry) and the in-memory Registry is updated so
subsequent ticks use the new values without restart. After each write we
broadcast a `config-update` WS message so live FE clients re-render with the
new metadata immediately instead of showing stale FDV until the next snapshot."""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from ..config import settings
from ..models import Asset, Market, RatioSource
from ..registry import get_registry

log = structlog.get_logger("app.api.admin")

# `[A-Z0-9_-]{1,32}` matches the asset_ids we actually emit (SPACEX, OPENAI,
# ANTHROPIC). Constraining at the path-parameter level blocks log injection
# via CR/LF in the id and rejects path-traversal attempts at the FastAPI
# layer (the id is also used only as a dict key, but this is defence in depth).
_ASSET_ID_REGEX = r"^[A-Z0-9_-]{1,32}$"
_EXCHANGE_REGEX = r"^[a-z0-9_-]{1,32}$"


async def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    expected = settings.admin_token
    if not expected:
        # Settings always provides one — either from env or generated. If we
        # somehow got here without one, fail closed.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="admin token not configured"
        )
    # `hmac.compare_digest` is constant-time — guards against the (mostly
    # theoretical on loopback, real-world once exposed on VPS) timing oracle
    # that bytewise `!=` would leak. Short-circuit `not x_admin_token` first
    # because compare_digest expects two non-empty operands of equal type.
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing X-Admin-Token"
        )


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


class UpdateSharesBody(BaseModel):
    # `allow_inf_nan=False` is critical — Pydantic v2 floats accept Infinity
    # and NaN by default; either would corrupt every downstream FDV.
    fully_diluted_shares: float = Field(gt=0, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=1024)
    source_url: str | None = Field(default=None, max_length=2048)
    confidence: int = Field(ge=0, le=100)


class UpdateRatioBody(BaseModel):
    share_ratio: float = Field(gt=0, allow_inf_nan=False)
    # Body type matches the on-disk literal so a typo ("garbage") is rejected
    # at request parsing rather than at the next reload().
    ratio_source: RatioSource
    ratio_reference_fdv: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    confidence: int = Field(ge=0, le=100)


async def _broadcast_config_update(request: Request) -> None:
    aggregator = getattr(request.app.state, "aggregator", None)
    if aggregator is None:
        return
    try:
        await aggregator.broadcast_config_update()
    except Exception as e:  # noqa: BLE001
        # Never fail the admin write because a broadcast hiccupped, but DO
        # log it — silent failure means the FE shows stale metadata with no
        # operator signal.
        log.warning("admin_broadcast_failed", error=repr(e))


@router.post("/asset/{asset_id}/shares", response_model=Asset)
async def update_asset_shares(
    body: UpdateSharesBody,
    request: Request,
    asset_id: str = Path(pattern=_ASSET_ID_REGEX),
) -> Asset:
    try:
        result = get_registry().update_asset_shares(
            asset_id=asset_id,
            fully_diluted_shares=body.fully_diluted_shares,
            source=body.source,
            confidence=body.confidence,
            source_url=body.source_url,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    log.info(
        "admin_shares_updated",
        asset_id=asset_id,
        shares=body.fully_diluted_shares,
        confidence=body.confidence,
    )
    await _broadcast_config_update(request)
    return result


@router.post("/market/{asset_id}/{exchange}/ratio", response_model=Market)
async def update_market_ratio(
    body: UpdateRatioBody,
    request: Request,
    asset_id: str = Path(pattern=_ASSET_ID_REGEX),
    exchange: str = Path(pattern=_EXCHANGE_REGEX),
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
    log.info(
        "admin_ratio_updated",
        asset_id=asset_id,
        exchange=exchange,
        share_ratio=body.share_ratio,
        ratio_source=body.ratio_source,
        confidence=body.confidence,
    )
    await _broadcast_config_update(request)
    return result
