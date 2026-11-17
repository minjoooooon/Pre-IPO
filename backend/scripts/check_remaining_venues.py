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
from app.adapters.bydfi import BYDFiAdapter
from app.adapters.bingx import BingXAdapter
from app.adapters.mexc import MEXCAdapter
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
    bydfi_state = 0
    bydfi_opening = 1
    async def bydfi(request):
        if "detailV2" in request.url.path:
            data = [{"symbol": "OPENAI_USDT", "state": bydfi_state, "ot": bydfi_opening, "cs": 0.01}]
        elif "funding_rate" in request.url.path:
            data = {"symbol": "OPENAI_USDT", "collectCycle": 8}
        else:
            data = {"symbol": "OPENAI_USDT", "lastPrice": 100, "amount24": 50000,
                    "holdVol": 1000, "fairPrice": 100, "fundingRate": 0.00005,
                    "timestamp": int(time.time() * 1000)}
        return httpx.Response(200, json={"success": True, "code": 0, "data": data})
    async with httpx.AsyncClient(transport=httpx.MockTransport(bydfi)) as client:
        tick = await BYDFiAdapter().fetch_tick(client, market("bydfi", "OPENAI_USDT"))
        assert tick.token_price == 100 and tick.vol24 == 50000 and tick.funding_interval_hours == 8
        assert tick.oi is None, "Undocumented holdVol units must not become USD OI"
        bydfi_state = 3
        try:
            await BYDFiAdapter().fetch_tick(client, market("bydfi", "OPENAI_USDT"))
            raise AssertionError("Delisted BYDFi contract emitted a price")
        except ValueError:
            pass
        bydfi_state, bydfi_opening = 0, (time.time() + 3600) * 1000
        try:
            await BYDFiAdapter().fetch_tick(client, market("bydfi", "OPENAI_USDT"))
            raise AssertionError("Future BYDFi listing emitted a price")
        except ValueError:
            pass
    async def bingx(request):
        data = ([{"symbol": "NCSKANTHROPIC2USD-USDT", "status": 25}]
                if request.url.path.endswith("contracts") else
                {"symbol": "NCSKANTHROPIC2USD-USDT", "lastPrice": "1300"})
        return httpx.Response(200, json={"code": 0, "data": data})
    async with httpx.AsyncClient(transport=httpx.MockTransport(bingx)) as client:
        assert await BingXAdapter()._fetch_ticker(client, "NCSKANTHROPIC2USD-USDT") is None, "Halted BingX feed must not refresh old prices"
    stale_accepted = []
    for adapter, symbol, time_key in [(BingXAdapter(), "NCSKOPENAI2USD-USDT", "closeTime"),
                                      (MEXCAdapter(), "OPENAI_USDT", "timestamp")]:
        source_ms = (time.time() - 3600) * 1000
        mexc_state = 0
        async def old_price(request):
            if request.url.path.endswith("contracts"):
                data = [{"symbol": symbol, "status": 1}]
            elif request.url.path.endswith("detail"):
                data = {"symbol": symbol, "state": mexc_state}
            else:
                data = {"symbol": symbol, "lastPrice": "1200", time_key: source_ms}
            return httpx.Response(200, json={"success": True, "code": 0, "data": data})
        async with httpx.AsyncClient(transport=httpx.MockTransport(old_price)) as client:
            if await adapter._fetch_ticker(client, symbol) is not None:
                stale_accepted.append(adapter.exchange_id)
            source_ms = int((time.time() - 2) * 1000)
            fresh = await adapter._fetch_ticker(client, symbol)
            assert fresh and fresh[time_key] == source_ms
            if adapter.exchange_id == "mexc":
                mexc_state = 2
                assert await adapter._fetch_ticker(client, symbol) is None, "Halted MEXC AI feed must not emit fresh-looking prices"
    assert not stale_accepted, f"Old source timestamps accepted: {stale_accepted}"
    rows = json.loads((Path(__file__).resolve().parents[1]/"data/markets.json").read_text(encoding="utf-8"))["markets"]
    assert not any(m["exchange"] in ("bitmart", "kucoin") for m in rows)
    print("PASS: current prelisting parsing, single-side USD OI, funding cadence, requested removals")


asyncio.run(checks())
