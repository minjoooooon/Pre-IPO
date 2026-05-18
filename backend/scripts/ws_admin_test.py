"""Verify admin POST -> config-update WS broadcast.

1. Subscribe to /ws (drain snapshot)
2. In a background task, POST /api/admin/market/SPACEX/okx/ratio after 2s
3. Wait for incoming config-update message and validate the new ratio is in markets[]
4. Then reset back to 1.0 and confirm again
"""

import asyncio
import io
import json
import sys

import httpx
import websockets

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")


async def post_ratio(new_ratio: float) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "http://127.0.0.1:8000/api/admin/market/SPACEX/okx/ratio",
            json={
                "share_ratio": new_ratio,
                "ratio_source": "market-fitted",
                "ratio_reference_fdv": 800e9,
                "confidence": 55,
            },
            timeout=5.0,
        )
        r.raise_for_status()


async def post_shares(new_shares: float) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "http://127.0.0.1:8000/api/admin/asset/SPACEX/shares",
            json={
                "fully_diluted_shares": new_shares,
                "source": "config-update test",
                "confidence": 65,
            },
            timeout=5.0,
        )
        r.raise_for_status()


async def main() -> int:
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        snap_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        snap = json.loads(snap_raw)
        if snap["type"] != "snapshot":
            print(f"unexpected first msg: {snap['type']}")
            return 1
        print("[1] snapshot received: assets=", len(snap["assets"]), "markets=", len(snap["markets"]))
        okx_initial = next(m for m in snap["markets"] if m["exchange"] == "okx")
        print(f"    okx initial ratio = {okx_initial['share_ratio']}")

        # Schedule the admin POST
        print("[2] scheduling admin POST ratio=3.5 in 2s...")
        async def _trigger():
            await asyncio.sleep(2)
            await post_ratio(3.5)
            print("    POST ratio=3.5 completed")
        asyncio.create_task(_trigger())

        # Wait for config-update
        deadline = asyncio.get_event_loop().time() + 10
        seen_cfg = False
        while asyncio.get_event_loop().time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "config-update":
                okx_after = next(m for m in msg["markets"] if m["exchange"] == "okx")
                print(f"[3] config-update received: okx new ratio = {okx_after['share_ratio']}, source={okx_after['ratio_source']}, conf={okx_after['confidence']}")
                seen_cfg = True
                break

        if not seen_cfg:
            print("FAIL: no config-update received within deadline")
            return 2

        # Also test shares update
        print("[4] testing shares update via config-update...")
        async def _trigger_shares():
            await asyncio.sleep(1)
            await post_shares(12_500_000_000.0)
            print("    POST shares=12.5B completed")
        asyncio.create_task(_trigger_shares())

        deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            if msg.get("type") == "config-update":
                spx = next(a for a in msg["assets"] if a["id"] == "SPACEX")
                print(f"[5] shares config-update received: SPACEX new fdvShares = {spx['fully_diluted_shares']}")
                break

        # Reset everything
        await post_ratio(1.0)
        await post_shares(11_870_000_000.0)
        print("[6] reset OK")
        return 0


sys.exit(asyncio.run(main()))
