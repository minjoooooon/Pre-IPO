from fastapi import APIRouter, HTTPException, Path, Query, Request

from ..config import settings
from ..models import AdapterStatus, Asset, Market
from ..registry import get_registry
from ..store import get_store

router = APIRouter(prefix="/api")

# Same character class as admin._ASSET_ID_REGEX — keep the two endpoints
# consistent so a path-traversal probe can't get further on the read side
# than it can on the admin side.
_ASSET_ID_PATTERN = r"^[A-Z0-9_-]{1,32}$"


@router.get("/assets", response_model=list[Asset])
def list_assets() -> list[Asset]:
    return get_registry().list_assets()


@router.get("/assets/{asset_id}", response_model=Asset)
def get_asset(asset_id: str = Path(pattern=_ASSET_ID_PATTERN)) -> Asset:
    asset = get_registry().get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return asset


@router.get("/markets", response_model=list[Market])
def list_markets(
    asset_id: str | None = Query(default=None, pattern=_ASSET_ID_PATTERN),
) -> list[Market]:
    return get_registry().list_markets(asset_id=asset_id)


@router.get("/status", response_model=list[AdapterStatus])
def adapter_statuses(request: Request) -> list[AdapterStatus]:
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        return []
    return supervisor.statuses()


@router.get("/history/{asset_id}")
async def history(
    asset_id: str = Path(pattern=_ASSET_ID_PATTERN),
    seconds: int | None = Query(default=None),
) -> dict:
    # Bound the window. Without this, a caller passing huge `seconds=` (or a
    # negative value that silently inverted the cutoff) could either drag
    # hundreds of thousands of rows into one JSON response or get an empty
    # response with no hint why.
    if seconds is None:
        seconds = settings.history_replay_seconds
    elif seconds <= 0:
        raise HTTPException(status_code=400, detail="seconds must be positive")
    else:
        seconds = min(seconds, settings.history_replay_seconds)
    asset = get_registry().get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    rows = await get_store().replay_recent(
        asset_id, seconds, limit=settings.history_max_rows
    )
    return {"asset_id": asset_id, "seconds": seconds, "count": len(rows), "ticks": rows}
