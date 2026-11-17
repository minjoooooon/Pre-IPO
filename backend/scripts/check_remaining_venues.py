"""Focused adapter/public-feed checks; no credentials or orders."""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.adapters.bybit import BybitAdapter
from app.adapters.gate_futures import GateFuturesAdapter
from app.models import Market


def market(exchange, symbol):
    return Market(asset_id="ANTHROPIC", exchange=exchange, symbol=symbol, venue="CEX",
                  structure="perp", ratio_source="uncalibrated", confidence=0, quote_only=True)


async def checks():
    async def bybit(request):
        meta = {"symbol": "ANTUSDT", "status": "PreLaunch", "fundingInterval": 480,
                "preListingInfo": {"curAuctionPhase": "ContinuousTrading"}}
        ticker = {"symbol": "ANTUSDT", "lastPrice": "100", "bid1Price": "99", "ask1Price": "101",
                  "turnover24h": "50000", "singleOpenInterestValue": "30000", "fundingRate": "0"}
        row = meta if "instruments-info" in request.url.path else ticker
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [row]}, "time": int(time.time()*1000)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(bybit)) as client:
        tick = await BybitAdapter().fetch_tick(client, market("bybit", "ANTUSDT"))
        assert tick.token_price == 100 and tick.oi == 30000 and tick.vol24 == 50000
        assert tick.funding_interval_hours == 8

    async def gate(request):
        meta = {"name": "ANT_USDT", "status": "trading", "quanto_multiplier": "0.01",
                "position_size": 1000, "funding_interval": 28800, "mark_price": "100"}
        ticker = {"contract": "ANT_USDT", "last": "101", "volume_24h_quote": "50000",
                  "total_size": "2000", "funding_rate": "0.00005"}
        return httpx.Response(200, json=meta if "contracts" in request.url.path else [ticker])
    async with httpx.AsyncClient(transport=httpx.MockTransport(gate)) as client:
        tick = await GateFuturesAdapter().fetch_tick(client, market("gate-futures", "ANT_USDT"))
        assert tick.token_price == 101 and tick.oi == 1000 and tick.vol24 == 50000
        assert tick.funding_interval_hours == 8
    rows = json.loads((Path(__file__).resolve().parents[1]/"data/markets.json").read_text(encoding="utf-8"))["markets"]
    assert not any(m["exchange"] in ("bitmart", "kucoin") for m in rows)
    print("PASS: current prelisting parsing, single-side USD OI, funding cadence, requested removals")


asyncio.run(checks())
