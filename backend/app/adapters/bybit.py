"""Bybit V5 public linear pre-market contracts (continuous trading only)."""
import asyncio
import time

from ..models import Tick
from .base import PublicRestAdapter
from .base import finite_number as num


class BybitAdapter(PublicRestAdapter):
    exchange_id = "bybit"
    symbols = {"ANTHROPIC": "ANTUSDT", "OPENAI": "OPENAIUSDT"}

    async def fetch_tick(self, client, market):
        base = "https://api.bybit.com/v5/market/"
        responses = await asyncio.gather(*[
            client.get(base + endpoint, params={"category": "linear", "symbol": market.symbol})
            for endpoint in ("instruments-info", "tickers")])
        rows = []
        for response in responses:
            response.raise_for_status()
            body = response.json()
            if body.get("retCode") != 0:
                raise ValueError("Bybit API rejected request")
            rows.append(next((x for x in body["result"]["list"] if x.get("symbol") == market.symbol), None))
        meta, ticker = rows
        if not meta or not ticker:
            raise ValueError("Bybit symbol absent")
        continuous = (meta.get("preListingInfo") or {}).get("curAuctionPhase") == "ContinuousTrading"
        if meta.get("status") != "Trading" and not (meta.get("status") == "PreLaunch" and continuous):
            raise ValueError("Bybit contract not in continuous trading")
        price = num(ticker.get("lastPrice"))
        if price is None or price <= 0:
            raise ValueError("Bybit invalid price")
        oi = num(ticker.get("singleOpenInterestValue"))
        if oi is None:
            both = num(ticker.get("openInterestValue"))
            oi = both / 2 if both is not None else None
        minutes = num(meta.get("fundingInterval"))
        hours = int(minutes / 60) if minutes and minutes % 60 == 0 else None
        return Tick(market_id=market.market_id, token_price=price,
                    bid=num(ticker.get("bid1Price")), ask=num(ticker.get("ask1Price")),
                    vol24=num(ticker.get("turnover24h")), oi=oi,
                    funding_rate=num(ticker.get("fundingRate")), funding_interval_hours=hours,
                    timestamp=time.time())
