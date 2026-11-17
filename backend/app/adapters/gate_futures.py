"""Gate USDT futures; distinct from Gate spot certificate adapter."""
import asyncio
import time
from .base import PublicRestAdapter, finite_number as num
from ..models import Tick


class GateFuturesAdapter(PublicRestAdapter):
    exchange_id = "gate-futures"
    symbols = {"ANTHROPIC": "ANT_USDT", "OPENAI": "OPENAI_USDT"}

    async def fetch_tick(self, client, market):
        base = "https://api.gateio.ws/api/v4/futures/usdt/"
        responses = await asyncio.gather(
            client.get(base + "contracts/" + market.symbol),
            client.get(base + "tickers", params={"contract": market.symbol}))
        for response in responses:
            response.raise_for_status()
        meta = responses[0].json()
        ticker = next((x for x in responses[1].json() if x.get("contract") == market.symbol), None)
        now = time.time()
        if meta.get("name") != market.symbol or not ticker or meta.get("status") != "trading":
            raise ValueError("Gate contract unavailable")
        end = num(meta.get("delisted_time"))
        if end and now >= end:
            raise ValueError("Gate contract delisted")
        price = num(ticker.get("last"))
        if price is None or price <= 0:
            raise ValueError("Gate invalid price")
        size, multiplier, mark = [num(meta.get(k)) for k in ("position_size", "quanto_multiplier", "mark_price")]
        oi = size * multiplier * mark if all(x is not None for x in (size, multiplier, mark)) else None
        seconds = num(meta.get("funding_interval"))
        hours = int(seconds / 3600) if seconds and seconds % 3600 == 0 else None
        return Tick(market_id=market.market_id, token_price=price,
                    bid=num(ticker.get("highest_bid")), ask=num(ticker.get("lowest_ask")),
                    vol24=num(ticker.get("volume_24h_quote")), oi=oi,
                    funding_rate=num(ticker.get("funding_rate")), funding_interval_hours=hours,
                    timestamp=now)
