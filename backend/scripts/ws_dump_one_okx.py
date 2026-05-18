"""Connect to WS and dump first 5 OKX ticks (full payload) to verify USD units."""

import asyncio
import io
import json
import sys

import websockets

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        # Skip snapshot
        _ = await asyncio.wait_for(ws.recv(), timeout=10.0)
        seen_ok = 0
        seen_bg = 0
        while seen_ok < 3 or seen_bg < 2:
            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
            msg = json.loads(raw)
            if msg.get("type") != "tick":
                continue
            q = msg["quote"]
            if q["exchange"] == "okx" and seen_ok < 3:
                print(f"OKX  px={q['token_price']} vol24={q['vol24']} oi={q['oi']} funding={q['funding_rate']}")
                seen_ok += 1
            elif q["exchange"] == "bitget" and seen_bg < 2:
                print(f"BGT  px={q['token_price']} vol24={q['vol24']} oi={q['oi']} funding={q['funding_rate']}")
                seen_bg += 1


asyncio.run(main())
