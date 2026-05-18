"""Normalize a raw adapter Tick into a calibrated NormalizedQuote.

Uses (asset.fully_diluted_shares, market.share_ratio) to derive
implied_share_price and implied_fdv. Uncalibrated markets emit a quote with
None FDV + calibration_status="uncalibrated" so the FE shows a warning
badge instead of a misleading number."""

from __future__ import annotations

import math
import time

from .config import settings
from .models import Asset, CalibrationStatus, Market, NormalizedQuote, Tick


def normalize(tick: Tick, market: Market, asset: Asset, now: float | None = None) -> NormalizedQuote:
    now = now if now is not None else time.time()
    spread_bp: float | None = None
    if tick.bid is not None and tick.ask is not None and tick.bid > 0:
        spread_bp = ((tick.ask - tick.bid) / tick.bid) * 10_000

    # Future-skewed timestamps (clock drift) are surfaced as stale rather than
    # silently "fresh", so the FE shows a warning instead of a confident chart.
    if tick.timestamp > now + settings.max_clock_skew_seconds:
        is_stale = True
    else:
        is_stale = (now - tick.timestamp) > settings.stale_threshold_seconds

    # share_ratio guards: None, "uncalibrated" source, zero, negative, or
    # non-finite all route to the uncalibrated branch so we never divide-by-zero
    # or emit a NaN/Inf FDV.
    bad_ratio = (
        market.share_ratio is None
        or market.ratio_source == "uncalibrated"
        or market.share_ratio <= 0
        or not math.isfinite(market.share_ratio)
    )
    if bad_ratio:
        return NormalizedQuote(
            asset_id=market.asset_id,
            exchange=market.exchange,
            token_price=tick.token_price,
            implied_share_price=None,
            implied_fdv=None,
            confidence=0,
            calibration_status="uncalibrated",
            vol24=tick.vol24,
            oi=tick.oi,
            funding_rate=tick.funding_rate,
            spread_bp=spread_bp,
            timestamp=tick.timestamp,
        )

    implied_share = tick.token_price / market.share_ratio
    implied_fdv = implied_share * asset.fully_diluted_shares
    status: CalibrationStatus = "stale" if is_stale else "calibrated"
    return NormalizedQuote(
        asset_id=market.asset_id,
        exchange=market.exchange,
        token_price=tick.token_price,
        implied_share_price=implied_share,
        implied_fdv=implied_fdv,
        confidence=market.confidence,
        calibration_status=status,
        vol24=tick.vol24,
        oi=tick.oi,
        funding_rate=tick.funding_rate,
        spread_bp=spread_bp,
        timestamp=tick.timestamp,
    )
