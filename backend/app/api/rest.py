from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..models import AdapterStatus, Asset, Market
from ..registry import get_registry
from ..store import get_store

router = APIRouter(prefix="/api")


@router.get("/assets", response_model=list[Asset])
def list_assets() -> list[Asset]:
    return get_registry().list_assets()


@router.get("/assets/{asset_id}", response_model=Asset)
def get_asset(asset_id: str) -> Asset:
    asset = get_registry().get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset_id={asset_id} not found")
    return asset


@router.get("/markets", response_model=list[Market])
def list_markets(asset_id: str | None = None) -> list[Market]:
    return get_registry().list_markets(asset_id=asset_id)


@router.get("/status", response_model=list[AdapterStatus])
def adapter_statuses(request: Request) -> list[AdapterStatus]:
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        return []
    return supervisor.statuses()


@router.get("/history/{asset_id}")
async def history(asset_id: str, seconds: int | None = None) -> dict:
    seconds = seconds or settings.history_replay_seconds
    asset = get_registry().get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset_id={asset_id} not found")
    rows = await get_store().replay_recent(asset_id, seconds)
    return {"asset_id": asset_id, "seconds": seconds, "count": len(rows), "ticks": rows}
