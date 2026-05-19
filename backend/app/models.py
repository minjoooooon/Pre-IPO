from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VenueType = Literal["DEX", "CEX"]
Structure = Literal["SPV-backed", "SPV-mirror", "synthetic", "mirror-credit", "perp"]
RatioSource = Literal["official", "whitepaper", "back-solved", "market-fitted", "uncalibrated"]
CalibrationStatus = Literal["calibrated", "uncalibrated", "stale"]
AdapterState = Literal["connected", "reconnecting", "disconnected", "stale", "disabled"]


class LastRound(BaseModel):
    date: str
    price: float
    valuation: float


class Asset(BaseModel):
    # `extra="allow"` so on-disk fields the schema hasn't formalized yet
    # (e.g. future `cik`, `dba`, custom audit columns) survive a round-trip
    # through the registry instead of being silently dropped on the next
    # admin save.
    model_config = ConfigDict(extra="allow")

    id: str
    ticker: str
    name: str
    sector: str
    fully_diluted_shares: float
    shares_source: str
    shares_source_url: str | None = None
    shares_confidence: int = Field(ge=0, le=100)
    shares_last_updated: date
    last_round: LastRound
    reference_share_price: float | None = None
    headline: str | None = None


class Market(BaseModel):
    # See Asset.model_config — same rationale for forward-compat extras.
    model_config = ConfigDict(extra="allow")

    asset_id: str
    exchange: str
    symbol: str
    venue: VenueType
    structure: Structure
    share_ratio: float | None = None
    ratio_source: RatioSource
    ratio_reference_fdv: float | None = None
    ratio_calibrated_at: datetime | None = None
    confidence: int = Field(ge=0, le=100)
    doc_url: str | None = None
    notes: str | None = None

    @property
    def market_id(self) -> str:
        return f"{self.asset_id}:{self.exchange}"


class Tick(BaseModel):
    """Raw tick emitted by an adapter — exchange-normalized but not asset-normalized."""

    market_id: str
    token_price: float
    bid: float | None = None
    ask: float | None = None
    vol24: float | None = None
    oi: float | None = None
    funding_rate: float | None = None
    timestamp: float  # unix seconds (UTC)


class NormalizedQuote(BaseModel):
    """Tick after share-ratio calibration applied. None FDV means uncalibrated."""

    model_config = ConfigDict(json_schema_extra={"description": "Calibrated quote"})

    asset_id: str
    exchange: str
    token_price: float
    implied_share_price: float | None
    implied_fdv: float | None
    confidence: int
    calibration_status: CalibrationStatus
    vol24: float | None = None
    oi: float | None = None
    funding_rate: float | None = None
    spread_bp: float | None = None
    timestamp: float


class AdapterStatus(BaseModel):
    exchange: str
    state: AdapterState
    last_tick_at: float | None = None
    reconnect_count: int = 0
    last_error: str | None = None
