"""BYDFi public website feed; no authenticated or trading endpoints.

Website transport is less stable than a published API. Unverified OI units
are deliberately omitted; prices alone never establish a valuation basis.
"""
import asyncio
import time

from ..models import Tick
from .base import PublicRestAdapter
from .base import finite_number as num


class BYDFiAdapter(PublicRestAdapter):
    exchange_id = "bydfi"
    symbols = {"OPENAI": "OPENAI_USDT"}

    async def fetch_tick(self, client, market):
        base = "https://www.bydfi.com/api/platform/futures/api/v1/contract/"
        responses = await asyncio.gather(
            client.get(base + "detailV2", params={"client": "web", "symbol": market.symbol}),
            client.get(base + "ticker", params={"symbol": market.symbol}),
            client.get(base + "funding_rate/" + market.symbol))
        data = []
        for response in responses:
            response.raise_for_status()
            body = response.json()
            if body.get("success") is not True or body.get("code") != 0:
                raise ValueError("BYDFi public feed rejected request")
            data.append(body["data"])
        meta = next((row for row in data[0] if row.get("symbol") == market.symbol), None)
        ticker, funding = data[1:]
        now = time.time()
        if not meta or meta.get("state") != 0 or ticker.get("symbol") != market.symbol:
            raise ValueError("BYDFi contract unavailable")
        opening = num(meta.get("ot"))
        if opening is None or opening / 1000 > now:
            raise ValueError("BYDFi contract has not opened")
        price, timestamp = num(ticker.get("lastPrice")), num(ticker.get("timestamp"))
        if price is None or price <= 0 or timestamp is None or not -30 <= now - timestamp / 1000 <= 60:
            raise ValueError("BYDFi invalid or stale ticker")
        hours = num(funding.get("collectCycle"))
        return Tick(market_id=market.market_id, token_price=price,
                    bid=num(ticker.get("bid1")), ask=num(ticker.get("ask1")),
                    vol24=num(ticker.get("amount24")), oi=None,
                    funding_rate=num(ticker.get("fundingRate")),
                    funding_interval_hours=int(hours) if hours and hours.is_integer() else None,
                    timestamp=now)
