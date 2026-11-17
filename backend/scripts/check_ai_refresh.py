"""Focused regression: venue valuation units are not actual company shares."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import Asset, Market, Tick
from app.normalizer import normalize

base = Path(__file__).resolve().parents[1] / "data"
assets = {a["id"]: Asset(**a) for a in json.loads((base / "assets.json").read_text(encoding="utf-8"))["assets"]}
markets = {f'{m["asset_id"]}:{m["exchange"]}': Market(**m) for m in json.loads((base / "markets.json").read_text(encoding="utf-8"))["markets"]}
market = markets["OPENAI:okx"]
tick = Tick(market_id=market.market_id, token_price=125, timestamp=100)
quote = normalize(tick, market, assets["OPENAI"], now=100)
assert quote.implied_fdv == 1_250_000_000_000, quote
assert quote.implied_share_price is None, "Venue unit must not imply a real share price"
for key in ("OPENAI:binance", "OPENAI:bitget-futures", "OPENAI:hl-vntl", "ANTHROPIC:hl-vntl"):
    m = markets[key]
    q = normalize(tick, m, assets[m.asset_id], now=100)
    assert q.implied_fdv == 125_000_000_000, (key, q)
for key in ("ANTHROPIC:binance", "ANTHROPIC:bitget-futures", "ANTHROPIC:mexc", "ANTHROPIC:bybit", "ANTHROPIC:kucoin", "ANTHROPIC:bydfi"):
    m = markets[key]
    q = normalize(tick, m, assets[m.asset_id], now=100)
    assert q.implied_fdv is None and q.calibration_status == "uncalibrated", key
    assert m.notes and m.doc_url
print("PASS: explicit valuation basis, no fictitious share price, new venues and transition exclusion")

# Historical normalization must not cross the reviewed pricing-basis boundary.
import asyncio
from unittest.mock import AsyncMock
from app.aggregator import Aggregator
from app.registry import Registry

async def check_history():
    registry = Registry(base)
    store = AsyncMock()
    cutoff = markets["OPENAI:okx"].rules_reviewed_at.timestamp()
    old = quote.model_dump()
    old.update(timestamp=cutoff - 1, implied_fdv=125e9)
    recent = {**old, "timestamp": cutoff + 1, "implied_fdv": 125e9, "implied_share_price": 52.0833}
    store.replay_recent.side_effect = lambda asset_id, *_: [old, recent] if asset_id == "OPENAI" else []
    agg = Aggregator(registry, store)
    await agg.start()
    try:
        points = agg.snapshot_message()["history"]["OPENAI"]["okx"]
        assert len(points) == 1 and points[0]["implied_fdv"] == 1250e9
        assert points[0]["implied_share_price"] is None
    finally:
        await agg.stop()

asyncio.run(check_history())
print("PASS: replay excludes obsolete normalization before the rules review")

