"""P1.5 calibration script.

For each (asset, exchange) market in markets.json:
  1. Run adapter.discover() — verify the symbol actually exists on the venue.
  2. If found, set share_ratio + ratio_source + ratio_reference_fdv with explicit
     rationale (printed to stdout for audit).
  3. If not found, leave uncalibrated and the adapter stays disabled.

Calibration policy (codex-reviewed):
  - DON'T backsolve all venues to match the reference FDV. That destroys the
    premium/discount signal the dashboard is designed to surface.
  - DO use the documented structural ratio when it's published (e.g. Republic
    explicitly states 1:1 for preSPAX → ratio=1.0, source=official).
  - DO use ratio=1.0 with source=market-fitted + ratio_reference_fdv as an
    audit anchor when only instrument metadata implies 1:1 (e.g. OKX ctVal=1).
  - DO leave uncalibrated/disabled when the symbol can't be reached.

The resulting implied FDVs may be wildly higher than the $800B tender — that's
the actual market signal (pre-IPO synthetics often trade at multiples of
private-round valuations). The dashboard's PremiumBars visualizes this gap."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Force UTF-8 on Windows console (default cp949 chokes on em-dash etc.)
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")
if isinstance(sys.stderr, io.TextIOWrapper):
    sys.stderr.reconfigure(encoding="utf-8")

# allow running as `python scripts/calibrate.py` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.base import DiscoverResult  # noqa: E402
from app.adapters.bitget import BitgetAdapter  # noqa: E402
from app.adapters.hyperliquid import HyperliquidAdapter  # noqa: E402
from app.adapters.okx import OkxAdapter  # noqa: E402
from app.config import settings  # noqa: E402
from app.registry import safe_write_json  # noqa: E402


def _strip_audit_segments(notes: str | None, *prefixes: str) -> str:
    """Remove pipe-separated audit segments whose body starts with any of
    `prefixes` (e.g. "[calibrated ", "[discover "). Used so a re-run of the
    calibration script replaces prior audit entries instead of appending
    indefinitely (markets.json already showed three duplicate "[calibrated
    2026-05-18]" entries before this fix landed)."""
    if not notes:
        return ""
    parts = [p.strip() for p in notes.split("|")]
    kept = [p for p in parts if p and not any(p.startswith(prefix) for prefix in prefixes)]
    return " | ".join(kept)


@dataclass
class CalibrationDecision:
    asset_id: str
    exchange: str
    found: bool
    share_ratio: float | None
    ratio_source: str
    ratio_reference_fdv: float | None
    confidence: int
    rationale: str
    last_price: float | None = None
    error: str | None = None


# Reference per asset: last_round.valuation from assets.json. Loaded at runtime
# rather than hardcoded, so the calibration uses the same "anchor" the asset
# itself documents (Bloomberg Dec 2025 tender for SpaceX, Oct 2025 tender for
# OpenAI, Series F for Anthropic, etc.).
_asset_refs: dict[str, float] = {}


def decide(asset_id: str, exchange: str, result: DiscoverResult) -> CalibrationDecision:
    reference_fdv = _asset_refs.get(asset_id)
    if not result.found:
        return CalibrationDecision(
            asset_id=asset_id,
            exchange=exchange,
            found=False,
            share_ratio=None,
            ratio_source="uncalibrated",
            ratio_reference_fdv=None,
            confidence=0,
            rationale=f"discover failed: {result.error}",
            last_price=None,
            error=result.error,
        )

    if exchange == "okx":
        # OKX SWAP instrument metadata: ctVal=1, ctValCcy=<asset>, ctMult=1.
        # 1 contract = 1 <asset> unit. No official OKX product brief states
        # shares-per-contract — flagged market-fitted with the asset's
        # most-recent reference round as the audit anchor.
        return CalibrationDecision(
            asset_id=asset_id,
            exchange=exchange,
            found=True,
            share_ratio=1.0,
            ratio_source="market-fitted",
            ratio_reference_fdv=reference_fdv,
            confidence=40,
            rationale=(
                f"OKX SWAP instrument metadata reports ctVal=1, ctValCcy={asset_id}, "
                f"ctMult=1 - literal interpretation = 1 contract represents 1 share "
                f"unit. Reference FDV anchor: ${(reference_fdv or 0)/1e9:.0f}B from "
                f"assets.json last_round.valuation. Override via /api/admin if needed."
            ),
            last_price=result.last_price,
        )

    if exchange == "bitget":
        # Bitget IPO Prime "pre<X>" tokens are issued via Republic as
        # mirror-credit instruments (1 token = 1 unit of post-IPO equity
        # performance economic exposure, no contract multiplier).
        return CalibrationDecision(
            asset_id=asset_id,
            exchange=exchange,
            found=True,
            share_ratio=1.0,
            ratio_source="official",
            ratio_reference_fdv=None,
            confidence=70,
            rationale=(
                f"Bitget IPO Prime / Republic mirror-credit structure: 1 token "
                f"= 1 unit of {asset_id} post-IPO stock performance. "
                f"ratio=1.0, source=official, confidence=70."
            ),
            last_price=result.last_price,
        )

    if exchange == "hyperliquid":
        # Should be unreachable since discover returns found=False, but kept
        # for completeness and future HIP-3 routing.
        return CalibrationDecision(
            asset_id=asset_id,
            exchange=exchange,
            found=True,
            share_ratio=None,
            ratio_source="uncalibrated",
            ratio_reference_fdv=None,
            confidence=0,
            rationale=(
                "Hyperliquid core perp universe does not list SPCX. SPCX is "
                "available only via HIP-3 builder-deployed markets (Trade.xyz / "
                "Ventuals). Routing not yet implemented."
            ),
            last_price=result.last_price,
        )

    return CalibrationDecision(
        asset_id=asset_id,
        exchange=exchange,
        found=result.found,
        share_ratio=None,
        ratio_source="uncalibrated",
        ratio_reference_fdv=None,
        confidence=0,
        rationale=f"no calibration policy defined for exchange={exchange}",
    )


async def run() -> int:
    markets_path = settings.data_dir / "markets.json"
    assets_path = settings.data_dir / "assets.json"
    with markets_path.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    with assets_path.open("r", encoding="utf-8") as f:
        assets_doc = json.load(f)

    # Populate per-asset reference FDV from last_round.valuation
    for a in assets_doc["assets"]:
        _asset_refs[a["id"]] = float(a["last_round"]["valuation"])

    adapters = {
        "hyperliquid": HyperliquidAdapter(),
        "okx": OkxAdapter(),
        "bitget": BitgetAdapter(),
    }

    decisions: list[CalibrationDecision] = []
    for m in doc["markets"]:
        asset_id = m["asset_id"]
        exchange = m["exchange"]
        adapter = adapters.get(exchange)
        print(f"\n--- {exchange} :: {asset_id} ---")
        if adapter is None:
            print(f"  no adapter for exchange={exchange}, skipping")
            continue
        result = await adapter.discover(asset_id)
        if result.found:
            print(f"  FOUND symbol={result.symbol}  last_price={result.last_price}")
            if result.detail:
                print(f"  detail={result.detail}")
        else:
            print(f"  MISSING  error={result.error}")

        decision = decide(asset_id, exchange, result)
        decisions.append(decision)
        print(f"  decision: ratio={decision.share_ratio}  source={decision.ratio_source}  conf={decision.confidence}")
        print(f"  rationale: {decision.rationale}")

    # Write back. Strip any prior `[calibrated ...]` / `[discover ...]`
    # segments before appending the new one so a re-run replaces rather than
    # accumulates (note bloat was visible in markets.json before this fix).
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for m in doc["markets"]:
        for d in decisions:
            if d.asset_id == m["asset_id"] and d.exchange == m["exchange"]:
                if d.share_ratio is not None:
                    m["share_ratio"] = d.share_ratio
                    m["ratio_source"] = d.ratio_source
                    m["ratio_reference_fdv"] = d.ratio_reference_fdv
                    m["ratio_calibrated_at"] = now_iso
                    m["confidence"] = d.confidence
                    cleaned = _strip_audit_segments(m.get("notes"), "[calibrated ", "[discover ")
                    addendum = f"[calibrated {now_iso[:10]}] {d.rationale}"
                    m["notes"] = f"{cleaned} | {addendum}" if cleaned else addendum
                else:
                    cleaned = _strip_audit_segments(m.get("notes"), "[calibrated ", "[discover ")
                    addendum = f"[discover {now_iso[:10]}] {d.rationale}"
                    m["notes"] = f"{cleaned} | {addendum}" if cleaned else addendum
                break

    # Use the same fsync + Windows-retry helper as the registry. Eliminates
    # the second atomicity story this codebase used to have.
    safe_write_json(markets_path, doc)
    print(f"\nWrote {markets_path}")

    # Summary
    print("\n=== SUMMARY ===")
    print(f"asset references: {_asset_refs}")
    print(f"calibrated venues: {sum(1 for d in decisions if d.share_ratio is not None)}/{len(decisions)}")
    for d in decisions:
        flag = "OK" if d.share_ratio is not None else "MISS"
        print(f"  [{flag}] {d.exchange:<12} {d.asset_id:<12} ratio={d.share_ratio}  source={d.ratio_source}  conf={d.confidence}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
